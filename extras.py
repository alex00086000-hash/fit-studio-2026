"""
Дополнения Grammika:
  * учёт пользователей (первый и последний визит) и статистика для владельца;
  * фото к записям дневника;
  * распознавание еды по фото через Claude API (включается переменной ANTHROPIC_API_KEY);
  * бот: /start и /stats через вебхук Telegram.

Подключается из server.py короткими вызовами — см. комментарии «extras» там.
"""
import base64
import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "").strip()
PUBLIC_URL = (os.environ.get("PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "").strip().rstrip("/")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
AI_MODEL = os.environ.get("AI_MODEL", "claude-haiku-5-5").strip()
AI_DAILY_LIMIT = int(os.environ.get("AI_DAILY_LIMIT", "15"))
PHOTO_MAX_BYTES = 400 * 1024       # фото в дневнике после сжатия на телефоне ~40–90 КБ
AI_PHOTO_MAX_BYTES = 1500 * 1024
SESSION_GAP = timedelta(minutes=30)  # новое открытие приложения = новый визит, если прошло 30 мин

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS app_users (
        telegram_id TEXT PRIMARY KEY,
        first_name TEXT,
        username TEXT,
        first_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
        last_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
        visits INTEGER NOT NULL DEFAULT 1
    )""",
    """CREATE TABLE IF NOT EXISTS meal_photos (
        meal_id BIGINT PRIMARY KEY,
        telegram_id TEXT NOT NULL,
        data BYTEA NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    "CREATE INDEX IF NOT EXISTS idx_meal_photos_user ON meal_photos (telegram_id)",
    """CREATE TABLE IF NOT EXISTS ai_usage (
        id BIGSERIAL PRIMARY KEY,
        telegram_id TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    "CREATE INDEX IF NOT EXISTS idx_ai_usage_user ON ai_usage (telegram_id, created_at)",
]


def init(pool):
    with pool.connection() as conn:
        for stmt in SCHEMA:
            conn.execute(stmt)
    threading.Thread(target=_register_webhook, daemon=True).start()


def is_admin(user_id):
    return bool(ADMIN_CHAT_ID) and str(user_id) == ADMIN_CHAT_ID


def features():
    return {"recognize": bool(ANTHROPIC_API_KEY), "photos": True}


# ---------------------------------------------------------------- пользователи

def user_info_from_init(init_data):
    """Имя и username из initData (подпись уже проверена в server.py)."""
    try:
        pairs = dict(urllib.parse.parse_qsl(init_data or "", keep_blank_values=True))
        u = json.loads(pairs.get("user", "{}"))
        return (str(u.get("first_name") or "")[:64] or None, str(u.get("username") or "")[:64] or None)
    except Exception:
        return None, None


def touch_user(cur, user_id, first_name=None, username=None):
    """Отмечает визит. Повторные запросы в течение 30 минут — тот же визит."""
    cur.execute(
        """INSERT INTO app_users (telegram_id, first_name, username) VALUES (%s, %s, %s)
           ON CONFLICT (telegram_id) DO UPDATE SET
             visits = app_users.visits + CASE WHEN app_users.last_seen < now() - interval '30 minutes'
                                              THEN 1 ELSE 0 END,
             last_seen = now(),
             first_name = COALESCE(EXCLUDED.first_name, app_users.first_name),
             username = COALESCE(EXCLUDED.username, app_users.username)""",
        (str(user_id), first_name, username),
    )


def stats(cur):
    """Сводка для владельца."""
    q = lambda sql, *a: (cur.execute(sql, a), cur.fetchone()[0])[1]
    out = {
        "total": q("SELECT COUNT(*) FROM app_users"),
        "new_today": q("SELECT COUNT(*) FROM app_users WHERE first_seen >= date_trunc('day', now())"),
        "new_7d": q("SELECT COUNT(*) FROM app_users WHERE first_seen >= now() - interval '7 days'"),
        "new_30d": q("SELECT COUNT(*) FROM app_users WHERE first_seen >= now() - interval '30 days'"),
        "active_today": q("SELECT COUNT(*) FROM app_users WHERE last_seen >= date_trunc('day', now())"),
        "active_7d": q("SELECT COUNT(*) FROM app_users WHERE last_seen >= now() - interval '7 days'"),
        "diary_users": q("SELECT COUNT(DISTINCT telegram_id) FROM meal_log"),
        "meals_today": q("SELECT COUNT(*) FROM meal_log WHERE date = to_char(now(), 'YYYY-MM-DD')"),
        "returning": q("SELECT COUNT(*) FROM app_users WHERE visits > 1"),
    }
    cur.execute(
        """SELECT to_char(d, 'YYYY-MM-DD'),
                  (SELECT COUNT(*) FROM app_users WHERE first_seen::date = d::date)
           FROM generate_series(date_trunc('day', now()) - interval '13 days',
                                date_trunc('day', now()), interval '1 day') d ORDER BY d"""
    )
    out["by_day"] = [{"date": r[0], "new": r[1]} for r in cur.fetchall()]
    cur.execute(
        """SELECT u.first_name, u.username, u.first_seen, u.last_seen, u.visits,
                  (SELECT COUNT(*) FROM meal_log m WHERE m.telegram_id = u.telegram_id)
           FROM app_users u ORDER BY u.last_seen DESC LIMIT 30"""
    )
    out["recent"] = [
        {"name": r[0] or "Без имени", "username": r[1] or "", "first_seen": _iso(r[2]),
         "last_seen": _iso(r[3]), "visits": r[4], "meals": r[5]}
        for r in cur.fetchall()
    ]
    return out


def _iso(v):
    if isinstance(v, datetime):
        return v.astimezone(timezone.utc).isoformat()
    return str(v)


def stats_text(s):
    return (
        "📊 Grammika — статистика\n\n"
        f"Всего пользователей: {s['total']}\n"
        f"Новых сегодня: {s['new_today']} · за 7 дней: {s['new_7d']} · за 30 дней: {s['new_30d']}\n"
        f"Заходили сегодня: {s['active_today']} · за 7 дней: {s['active_7d']}\n"
        f"Вернулись повторно: {s['returning']}\n"
        f"Ведут дневник питания: {s['diary_users']}\n"
        f"Записей еды сегодня: {s['meals_today']}"
    )


# ---------------------------------------------------------------- фото в дневнике

def _decode_jpeg(b64, limit):
    if not isinstance(b64, str):
        raise ValueError("no image")
    if b64.startswith("data:"):
        b64 = b64.split(",", 1)[1]
    raw = base64.b64decode(b64, validate=False)
    if len(raw) > limit:
        raise ValueError("image too large")
    if not (raw[:3] == b"\xff\xd8\xff" or raw[:8] == b"\x89PNG\r\n\x1a\n" or raw[8:12] == b"WEBP"):
        raise ValueError("not an image")
    return raw


def save_meal_photo(cur, user_id, meal_id, b64):
    raw = _decode_jpeg(b64, PHOTO_MAX_BYTES)
    cur.execute("SELECT 1 FROM meal_log WHERE id = %s AND telegram_id = %s", (int(meal_id), user_id))
    if not cur.fetchone():
        raise ValueError("no such meal")
    cur.execute(
        """INSERT INTO meal_photos (meal_id, telegram_id, data) VALUES (%s, %s, %s)
           ON CONFLICT (meal_id) DO UPDATE SET data = EXCLUDED.data, created_at = now()""",
        (int(meal_id), user_id, raw),
    )


def delete_meal_photo(cur, user_id, meal_id):
    cur.execute("DELETE FROM meal_photos WHERE meal_id = %s AND telegram_id = %s", (int(meal_id), user_id))


def get_meal_photo(cur, user_id, meal_id):
    cur.execute("SELECT data FROM meal_photos WHERE meal_id = %s AND telegram_id = %s",
                (int(meal_id), user_id))
    row = cur.fetchone()
    return bytes(row[0]) if row else None


def photo_ids(cur, user_id, meal_ids):
    if not meal_ids:
        return set()
    cur.execute("SELECT meal_id FROM meal_photos WHERE telegram_id = %s AND meal_id = ANY(%s::bigint[])",
                (user_id, list(meal_ids)))
    return {r[0] for r in cur.fetchall()}


def delete_user(cur, user_id):
    for table in ("meal_photos", "ai_usage", "app_users"):
        cur.execute(f"DELETE FROM {table} WHERE telegram_id = %s", (user_id,))


# ---------------------------------------------------------------- распознавание еды

AI_PROMPT = """На фото еда. Определи, что на тарелке, и оцени вес каждой позиции в граммах.
Ответь ТОЛЬКО JSON без пояснений, в формате:
{"items":[{"name":"Гречка отварная","grams":180,"kcal100":110,"p100":4.2,"f100":1.1,"c100":21.3}],"note":"короткая подсказка или пустая строка"}
Правила:
- name — по-русски, коротко и понятно (как в дневнике питания), без брендов, если их не видно.
- kcal100, p100, f100, c100 — калорийность и БЖУ на 100 г готового продукта по стандартным справочникам.
- grams — твоя оценка веса порции на фото; если не уверен, оцени по размеру тарелки и столовых приборов.
- Соусы, масло и напитки указывай отдельными позициями, если они видны.
- Если еды на фото нет, верни {"items":[],"note":"На фото не видно еды"}."""


class AiLimit(Exception):
    pass


class AiError(Exception):
    pass


def ai_quota_left(cur, user_id):
    cur.execute("SELECT COUNT(*) FROM ai_usage WHERE telegram_id = %s AND created_at > now() - interval '1 day'",
                (user_id,))
    return max(0, AI_DAILY_LIMIT - cur.fetchone()[0])


def recognize(pool, user_id, b64):
    if not ANTHROPIC_API_KEY:
        raise AiError("Распознавание пока не подключено")
    raw = _decode_jpeg(b64, AI_PHOTO_MAX_BYTES)
    media = "image/png" if raw[:4] == b"\x89PNG" else ("image/webp" if raw[8:12] == b"WEBP" else "image/jpeg")
    with pool.connection() as conn:
        with conn.cursor() as cur:
            if not is_admin(user_id) and ai_quota_left(cur, user_id) <= 0:
                raise AiLimit()
            cur.execute("INSERT INTO ai_usage (telegram_id) VALUES (%s)", (user_id,))
    body = {
        "model": AI_MODEL,
        "max_tokens": 1200,
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": media,
                                         "data": base64.b64encode(raw).decode()}},
            {"type": "text", "text": AI_PROMPT},
        ]}],
    }
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(),
        headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            resp = json.load(r)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = json.loads(exc.read().decode()).get("error", {}).get("message", "")[:200]
        except Exception:
            pass
        print("Распознавание: HTTP", exc.code, detail, flush=True)
        raise AiError("Сервис распознавания ответил ошибкой. Попробуйте позже.")
    except Exception as exc:
        print("Распознавание:", type(exc).__name__, flush=True)
        raise AiError("Сервис распознавания не ответил. Попробуйте ещё раз.")
    text = "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")
    return parse_ai_answer(text)


def _f(v, lo, hi):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return round(x, 1) if lo <= x <= hi else None


def parse_ai_answer(text):
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise AiError("Не удалось разобрать ответ. Попробуйте другое фото.")
    try:
        data = json.loads(m.group(0))
    except ValueError:
        raise AiError("Не удалось разобрать ответ. Попробуйте другое фото.")
    items = []
    for it in (data.get("items") or [])[:12]:
        name = str(it.get("name") or "").strip()[:80]
        kcal = _f(it.get("kcal100"), 0, 950)
        grams = _f(it.get("grams"), 1, 3000)
        if not name or kcal is None or grams is None:
            continue
        items.append({"name": name, "grams": round(grams), "cal": kcal,
                      "p": _f(it.get("p100"), 0, 100) or 0, "f": _f(it.get("f100"), 0, 100) or 0,
                      "c": _f(it.get("c100"), 0, 100) or 0})
    return {"items": items, "note": str(data.get("note") or "")[:200]}


# ---------------------------------------------------------------- бот

def _webhook_secret():
    return hashlib.sha256(("grammika-webhook:" + BOT_TOKEN).encode()).hexdigest()[:48]


def _tg(method, payload):
    req = urllib.request.Request("https://api.telegram.org/bot" + BOT_TOKEN + "/" + method,
                                 data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def _register_webhook():
    if not (BOT_TOKEN and PUBLIC_URL.startswith("https://")):
        return
    try:
        _tg("setWebhook", {"url": PUBLIC_URL + "/tg/webhook", "secret_token": _webhook_secret(),
                           "allowed_updates": ["message"], "drop_pending_updates": True})
        _tg("setMyCommands", {"commands": [{"command": "start", "description": "Открыть Grammika"}]})
        print("Бот: вебхук подключён", flush=True)
    except Exception as exc:
        print("Бот: вебхук не подключился:", type(exc).__name__, flush=True)


def check_webhook_secret(header_value):
    return bool(BOT_TOKEN) and header_value == _webhook_secret()


def handle_update(pool, update):
    msg = update.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    text = (msg.get("text") or "").strip()
    if not chat_id or not text.startswith("/"):
        return
    cmd = text.split()[0].split("@")[0].lower()
    if cmd == "/stats":
        if not is_admin(chat_id):
            return
        with pool.connection() as conn:
            with conn.cursor() as cur:
                reply = stats_text(stats(cur))
        _tg("sendMessage", {"chat_id": chat_id, "text": reply})
    elif cmd == "/start":
        payload = {"chat_id": chat_id,
                   "text": "Привет! Grammika — дневник питания, тренировки и прогресс в одном месте. "
                           "Нажмите кнопку, чтобы открыть приложение."}
        if PUBLIC_URL.startswith("https://"):
            payload["reply_markup"] = {"inline_keyboard": [[
                {"text": "Открыть Grammika", "web_app": {"url": PUBLIC_URL + "/"}}]]}
        _tg("sendMessage", payload)
