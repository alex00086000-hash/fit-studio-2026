from datetime import datetime, timedelta
import hashlib
import hmac
import http.server
import json
import os
import random
import re
import sys
import time
import urllib.parse

import psycopg
from psycopg_pool import ConnectionPool, PoolTimeout

PORT = int(os.environ.get("PORT", 8000))
# Токен бота из @BotFather. Если задан, все запросы проверяются по подписи Telegram.
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
# Строка подключения к Postgres (Neon): postgresql://user:password@host/dbname?sslmode=require
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
POOL_MAX = max(1, int(os.environ.get("POOL_MAX", "5")))
POOL = None  # создаётся в open_pool()
MAX_BODY = 5 * 1024 * 1024
INIT_DATA_MAX_AGE = 24 * 60 * 60

MEAL_TYPES = {"breakfast", "lunch", "dinner", "snack"}
SETTINGS_NUMERIC = {
    "target_weight": (20, 400),
    "target_cal": (500, 10000),
    "target_p": (0, 1000),
    "target_f": (0, 1000),
    "target_c": (0, 2000),
    "target_water": (0, 10000),
}
EXCLUDABLE = {"fish", "meat", "dairy"}
SETTINGS_KEYS = set(SETTINGS_NUMERIC) | {"onboarded", "excluded"}

DEFAULT_FOODS = [
    ("Куриное филе грудки (вареное/печеное)", 165, 31.0, 3.6, 0.0, "meat"),
    ("Яйцо вареное (2 шт ~ 100г)", 155, 13.0, 11.0, 1.1, "dairy"),
    ("Творог 5%", 121, 17.0, 5.0, 3.0, "dairy"),
    ("Гречневая каша на воде", 110, 4.2, 1.1, 21.3, "grains"),
    ("Рис белый вареный", 130, 2.7, 0.3, 28.0, "grains"),
    ("Овсяная каша на воде", 88, 3.0, 1.7, 15.0, "grains"),
    ("Макароны твердых сортов (вареные)", 140, 5.5, 0.7, 28.0, "grains"),
    ("Говядина постная (отварная)", 187, 26.0, 9.0, 0.0, "meat"),
    ("Индейка филе (готовое)", 150, 29.0, 3.0, 0.0, "meat"),
    ("Рыба белая (треска/минтай)", 82, 17.5, 0.7, 0.0, "fish"),
    ("Сыр твердый 45%", 350, 25.0, 28.0, 0.5, "dairy"),
    ("Хлеб цельнозерновой", 220, 7.0, 1.5, 43.0, "grains"),
    ("Картофель отварной / запеченный", 82, 2.0, 0.4, 16.7, "grains"),
    ("Огурцы свежие", 15, 0.8, 0.1, 2.8, "veg"),
    ("Помидоры свежие", 20, 0.9, 0.2, 3.9, "veg"),
    ("Яблоко свежее", 52, 0.3, 0.2, 13.8, "fruit"),
    ("Банан", 95, 1.5, 0.2, 21.8, "fruit"),
]

DEFAULT_WORKOUTS = [
    ("Быстрая прогулка / прогулка с собакой", 4.8),
    ("Спокойная пешая ходьба", 3.5),
    ("Приседания и выпады (дома)", 6.5),
    ("Отжимания и базовые упражнения", 6.0),
    ("Планка и пресс", 5.0),
    ("Комплексная разминка / зарядка", 3.2),
    ("Интенсивное домашнее кардио (HIIT)", 9.0),
]

DEFAULT_EXERCISES = [
    "Приседания со штангой",
    "Жим лёжа",
    "Становая тяга",
    "Жим стоя",
    "Тяга штанги в наклоне",
    "Румынская тяга",
    "Жим ногами",
    "Жим гантелей лёжа",
    "Тяга верхнего блока",
    "Подтягивания",
    "Отжимания",
    "Отжимания на брусьях",
    "Выпады с гантелями",
    "Сгибание рук с гантелями",
    "Разгибание рук на блоке",
]

MEAL_TEMPLATES = {
    "breakfast": [
        [
            ("Овсяная каша на воде", 0.55),
            ("Яйцо вареное (2 шт ~ 100г)", 0.45),
        ],
        [
            ("Яйцо вареное (2 шт ~ 100г)", 0.45),
            ("Хлеб цельнозерновой", 0.35),
            ("Сыр твердый 45%", 0.20),
        ],
        [
            ("Творог 5%", 0.60),
            ("Банан", 0.40),
        ],
    ],
    "lunch": [
        [
            ("Куриное филе грудки (вареное/печеное)", 0.45),
            ("Гречневая каша на воде", 0.45),
            ("Огурцы свежие", 0.10),
        ],
        [
            ("Индейка филе (готовое)", 0.45),
            ("Рис белый вареный", 0.45),
            ("Помидоры свежие", 0.10),
        ],
        [
            ("Рыба белая (треска/минтай)", 0.50),
            ("Картофель отварной / запеченный", 0.40),
            ("Огурцы свежие", 0.10),
        ],
    ],
    "dinner": [
        [
            ("Куриное филе грудки (вареное/печеное)", 0.55),
            ("Картофель отварной / запеченный", 0.35),
            ("Помидоры свежие", 0.10),
        ],
        [
            ("Индейка филе (готовое)", 0.55),
            ("Гречневая каша на воде", 0.35),
            ("Огурцы свежие", 0.10),
        ],
        [
            ("Рыба белая (треска/минтай)", 0.60),
            ("Рис белый вареный", 0.30),
            ("Огурцы свежие", 0.10),
        ],
    ],
    "snack": [
        [
            ("Творог 5%", 0.65),
            ("Яблоко свежее", 0.35),
        ],
        [
            ("Хлеб цельнозерновой", 0.45),
            ("Сыр твердый 45%", 0.35),
            ("Яблоко свежее", 0.20),
        ],
        [
            ("Творог 5%", 0.55),
            ("Банан", 0.45),
        ],
    ],
}


# ---------------------------------------------------------------- database

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS settings (
        telegram_id TEXT NOT NULL,
        key TEXT NOT NULL,
        value TEXT,
        PRIMARY KEY (telegram_id, key)
    )""",
    """CREATE TABLE IF NOT EXISTS entries (
        id BIGSERIAL PRIMARY KEY,
        telegram_id TEXT NOT NULL,
        date TEXT NOT NULL,
        weight DOUBLE PRECISION DEFAULT 0,
        calories INTEGER DEFAULT 0,
        steps INTEGER DEFAULT 0,
        note TEXT DEFAULT '',
        water_ml INTEGER DEFAULT 0,
        UNIQUE (telegram_id, date)
    )""",
    """CREATE TABLE IF NOT EXISTS meal_log (
        id BIGSERIAL PRIMARY KEY,
        telegram_id TEXT NOT NULL,
        date TEXT NOT NULL,
        meal_type TEXT DEFAULT 'breakfast',
        food_name TEXT,
        grams DOUBLE PRECISION,
        calories DOUBLE PRECISION,
        protein DOUBLE PRECISION DEFAULT 0,
        fat DOUBLE PRECISION DEFAULT 0,
        carbs DOUBLE PRECISION DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS workout_log (
        id BIGSERIAL PRIMARY KEY,
        telegram_id TEXT NOT NULL,
        date TEXT NOT NULL,
        workout_name TEXT,
        duration_min DOUBLE PRECISION,
        calories_burned DOUBLE PRECISION
    )""",
    """CREATE TABLE IF NOT EXISTS custom_foods (
        id BIGSERIAL PRIMARY KEY,
        telegram_id TEXT NOT NULL,
        name TEXT,
        calories DOUBLE PRECISION,
        protein DOUBLE PRECISION,
        fat DOUBLE PRECISION,
        carbs DOUBLE PRECISION
    )""",
    """CREATE TABLE IF NOT EXISTS strength_sets (
        id BIGSERIAL PRIMARY KEY,
        telegram_id TEXT NOT NULL,
        date TEXT NOT NULL,
        exercise TEXT,
        weight DOUBLE PRECISION,
        reps INTEGER
    )""",
    "CREATE INDEX IF NOT EXISTS idx_meal_user_date ON meal_log (telegram_id, date)",
    "CREATE INDEX IF NOT EXISTS idx_workout_user_date ON workout_log (telegram_id, date)",
    "CREATE INDEX IF NOT EXISTS idx_sets_user_date ON strength_sets (telegram_id, date)",
    "CREATE INDEX IF NOT EXISTS idx_custom_user ON custom_foods (telegram_id)",
]


def open_pool():
    """Создаёт пул соединений с Postgres. Без DATABASE_URL сервер не запускается."""
    global POOL
    if not DATABASE_URL:
        sys.exit(
            "ОШИБКА: не задана переменная окружения DATABASE_URL "
            "(строка подключения Neon, начинается с postgresql://)."
        )
    POOL = ConnectionPool(
        DATABASE_URL,
        min_size=1,
        max_size=POOL_MAX,
        # Neon усыпляет бесплатную базу через 5 минут простоя: закрываем простаивающие
        # соединения заранее и проверяем соединение при выдаче из пула.
        max_idle=240,
        max_lifetime=1800,
        timeout=30,
        check=ConnectionPool.check_connection,
        # prepare_threshold=None нужен для пулера Neon (PgBouncer).
        kwargs={"connect_timeout": 15, "prepare_threshold": None},
        open=False,
    )
    try:
        POOL.open(wait=True, timeout=60)
    except PoolTimeout:
        sys.exit(
            "ОШИБКА: не удалось подключиться к базе за 60 секунд. "
            "Проверьте DATABASE_URL (пароль, адрес, ?sslmode=require)."
        )


def init_db():
    with POOL.connection() as conn:
        for stmt in SCHEMA:
            conn.execute(stmt)


# ---------------------------------------------------------------- helpers

def valid_date(s):
    if not isinstance(s, str) or not re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return False
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def need_date(s):
    if not valid_date(s):
        raise ValueError("bad date")
    return s


def num(v, lo, hi):
    x = float(v)
    if x != x or x < lo or x > hi:
        raise ValueError("out of range")
    return x


def need_text(v, max_len=100):
    s = str(v).strip()
    if not s or len(s) > max_len:
        raise ValueError("bad text")
    return s


def verify_init_data(init_data, token, max_age=INIT_DATA_MAX_AGE):
    """Проверка подписи Telegram WebApp initData. Возвращает id пользователя или None."""
    if not init_data:
        return None
    try:
        pairs = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
        received = pairs.pop("hash", None)
        if not received:
            return None
        check_string = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
        secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        calc = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc, received):
            return None
        if time.time() - int(pairs.get("auth_date", "0")) > max_age:
            return None
        uid = json.loads(pairs.get("user", "{}")).get("id")
        return str(uid) if uid else None
    except Exception:
        return None


def extract_user_id(init_data_str, fallback_id="default_user"):
    """Режим разработки (без BOT_TOKEN): id берётся без проверки подписи."""
    fallback_id = str(fallback_id)[:64] or "default_user"
    if not init_data_str:
        return fallback_id
    try:
        parsed = dict(urllib.parse.parse_qsl(init_data_str, keep_blank_values=True))
        user_obj = json.loads(parsed.get("user", "{}"))
        uid = user_obj.get("id")
        return str(uid) if uid else fallback_id
    except Exception:
        return fallback_id


def get_user_settings(cur, user_id):
    cur.execute("SELECT key, value FROM settings WHERE telegram_id = %s", (user_id,))
    rows = dict(cur.fetchall())
    try:
        excluded = json.loads(rows.get("excluded", "[]"))
        if not isinstance(excluded, list):
            excluded = []
    except ValueError:
        excluded = []
    return {
        "onboarded": rows.get("onboarded", "0"),
        "target_weight": float(rows.get("target_weight", 73.0)),
        "target_cal": int(float(rows.get("target_cal", 1850))),
        "target_p": int(float(rows.get("target_p", 138))),
        "target_f": int(float(rows.get("target_f", 62))),
        "target_c": int(float(rows.get("target_c", 185))),
        "target_water": int(float(rows.get("target_water", 2250))),
        "excluded": excluded,
    }


def clean_setting(key, value):
    """Возвращает строку для хранения или бросает ValueError."""
    if key == "onboarded":
        return "1" if str(value) == "1" else "0"
    if key == "excluded":
        if not isinstance(value, list):
            raise ValueError("excluded must be a list")
        return json.dumps([v for v in value if v in EXCLUDABLE])
    lo, hi = SETTINGS_NUMERIC[key]
    return str(num(value, lo, hi))


def sync_daily_calories(cur, user_id, date_str):
    cur.execute(
        "SELECT SUM(calories) FROM meal_log WHERE telegram_id = %s AND date = %s",
        (user_id, date_str),
    )
    total_cal = int(round(cur.fetchone()[0] or 0))
    cur.execute(
        """INSERT INTO entries (telegram_id, date, weight, calories, steps, note, water_ml)
           VALUES (%s, %s, 0, %s, 0, '', 0)
           ON CONFLICT(telegram_id, date) DO UPDATE SET calories = excluded.calories""",
        (user_id, date_str, total_cal),
    )


def compute_streak(cur, user_id, today_str):
    """Дней подряд с любой активностью. Сегодняшний день без записей серию не обрывает."""
    cur.execute(
        """SELECT date FROM entries
             WHERE telegram_id = %s AND (calories > 0 OR water_ml > 0 OR weight > 0 OR steps > 0)
           UNION SELECT date FROM workout_log WHERE telegram_id = %s
           UNION SELECT date FROM strength_sets WHERE telegram_id = %s""",
        (user_id, user_id, user_id),
    )
    dates = {r[0] for r in cur.fetchall()}
    fmt = "%Y-%m-%d"
    day = datetime.strptime(today_str, fmt)
    if day.strftime(fmt) not in dates:
        day -= timedelta(days=1)
    streak = 0
    while day.strftime(fmt) in dates and streak < 3650:
        streak += 1
        day -= timedelta(days=1)
    return streak


# ---------------------------------------------------------------- web app

WEB_APP_HTML = r"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<meta name="theme-color" content="#F3F6F4">
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<title>Grammika — Умный трекер питания</title>
<style>
  @property --p { syntax: '<number>'; inherits: false; initial-value: 0; }
  :root {
    --bg: #F3F6F4;
    --card: #FFFFFF;
    --card2: #F5F8F6;
    --input: #EDF2EF;
    --accent: #1FA971;
    --accent-dark: #168A5B;
    --accent-soft: #E1F4EB;
    --on-accent: #FFFFFF;
    --text: #18261F;
    --muted: #74857B;
    --warn: #E5484D;
    --blue: #3B82F6;
    --amber: #F59E0B;
    --green: #1FA971;
    --border: #E1E9E4;
    --shadow: 0 2px 12px rgba(24, 38, 31, 0.07);
    --transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
  }
  * { box-sizing: border-box; font-family: -apple-system, 'Segoe UI', system-ui, Roboto, sans-serif; -webkit-tap-highlight-color: transparent; }
  body { margin: 0; padding: 12px 14px 96px; background: var(--bg); color: var(--text); transition: var(--transition); }
  .container { max-width: 580px; margin: 0 auto; }

  .onboard-backdrop, .modal-backdrop {
    position: fixed; inset: 0; background: rgba(24,38,31,0.45); z-index: 1000;
    display: none; align-items: center; justify-content: center; padding: 16px;
    backdrop-filter: blur(6px); opacity: 0; transition: opacity 0.3s ease;
    overflow-y: auto;
  }
  .onboard-backdrop.open, .modal-backdrop.open { display: flex; opacity: 1; }

  .onboard-card, .modal-card {
    background: var(--card); color: var(--text); width: 100%; max-width: 440px;
    border-radius: 24px; padding: 24px; border: 1px solid var(--border);
    box-shadow: 0 24px 48px rgba(24,38,31,0.18);
    transform: translateY(20px) scale(0.96); transition: transform 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
  }
  .onboard-backdrop.open .onboard-card, .modal-backdrop.open .modal-card { transform: translateY(0) scale(1); }
  .onboard-card h2, .modal-top h3 { margin: 0 0 6px; color: var(--text); font-size: 20px; font-weight: 800; }
  .onboard-card p { font-size: 13px; color: var(--muted); margin-bottom: 16px; }
  .modal-top { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
  .close-btn { background: var(--input); color: var(--muted); border: none; border-radius: 50%; width: 32px; height: 32px; cursor: pointer; font-weight: 800; }

  .top-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; gap: 8px; }
  .brand-title { font-size: 19px; font-weight: 900; letter-spacing: -0.3px; display: flex; align-items: center; gap: 8px; color: var(--text); flex-wrap: wrap; }
  .logo-dot { width: 26px; height: 26px; border-radius: 9px; background: linear-gradient(135deg, #34D399, #1FA971); display: inline-flex; align-items: center; justify-content: center; color: #fff; font-size: 14px; font-weight: 900; box-shadow: 0 3px 8px rgba(31,169,113,0.35); }
  .date-pill { display: flex; align-items: center; background: var(--card); border: 1px solid var(--border); color: var(--text); padding: 6px 12px; border-radius: 20px; gap: 6px; box-shadow: var(--shadow); }
  .date-pill input { background: transparent; border: none; color: var(--text); font-weight: 700; font-size: 13px; width: 112px; outline: none; margin: 0; padding: 0; }

  .wk-strip { display: grid; grid-template-columns: repeat(7, 1fr); gap: 6px; margin-bottom: 14px; }
  .wk-day { background: var(--card); border: 1px solid var(--border); border-radius: 14px; padding: 8px 0 9px; display: flex; flex-direction: column; align-items: center; gap: 3px; color: var(--muted); font-size: 11px; font-weight: 600; cursor: pointer; position: relative; box-shadow: var(--shadow); transition: transform 0.15s ease; }
  .wk-day:active { transform: scale(0.94); }
  .wk-day b { font-size: 15px; color: var(--text); font-weight: 800; }
  .wk-day.sel { background: var(--accent); border-color: var(--accent); color: rgba(255,255,255,0.85); box-shadow: 0 6px 14px rgba(31,169,113,0.3); }
  .wk-day.sel b { color: #fff; }
  .wk-day.today:not(.sel)::after { content: ""; position: absolute; bottom: 3px; width: 4px; height: 4px; border-radius: 50%; background: var(--accent); }

  .fitia-hero { background: var(--card); border: 1px solid var(--border); color: var(--text); border-radius: 24px; padding: 18px; margin-bottom: 14px; box-shadow: var(--shadow); }
  .cal-summary { display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; text-align: center; gap: 8px; margin-bottom: 16px; }
  .cal-stat .num { font-size: 20px; font-weight: 800; }
  .cal-stat .lbl { font-size: 11px; color: var(--muted); font-weight: 600; }

  .ring-box { position: relative; width: 128px; height: 128px; display: flex; flex-direction: column; align-items: center; justify-content: center; border-radius: 50%; background: conic-gradient(var(--ring, var(--accent)) calc(var(--p, 0) * 1%), var(--input) 0); transition: --p 0.6s ease; }
  .ring-box::before { content: ""; position: absolute; inset: 10px; border-radius: 50%; background: var(--card); }
  .ring-box > * { position: relative; z-index: 1; }
  .ring-box.over { --ring: var(--warn); }
  .ring-val { font-size: 27px; font-weight: 900; line-height: 1; color: var(--text); }
  .ring-box.over .ring-val { color: var(--warn); }
  .ring-lbl { font-size: 10px; color: var(--muted); margin-top: 4px; text-transform: uppercase; font-weight: 700; letter-spacing: 0.4px; }

  .macros-row { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
  .macro-card { background: var(--card2); border: 1px solid var(--border); padding: 10px; border-radius: 14px; }
  .macro-head { display: flex; justify-content: space-between; gap: 4px; font-size: 10px; font-weight: 700; margin-bottom: 6px; color: var(--text); white-space: nowrap; }
  .p-bar-bg { width: 100%; height: 7px; background: var(--input); border-radius: 6px; overflow: hidden; }
  .p-bar-fill { height: 100%; width: 0%; border-radius: 6px; transition: width 0.5s cubic-bezier(0.4, 0, 0.2, 1); }
  .macro-sub { font-size: 11px; color: var(--muted); margin-top: 5px; text-align: right; font-weight: 600; }

  .ai-banner { background: linear-gradient(135deg, #2BC98B 0%, #1B9D6A 100%); border: none; color: #fff; border-radius: 20px; padding: 15px 16px; margin-bottom: 14px; display: flex; justify-content: space-between; align-items: center; gap: 10px; box-shadow: 0 8px 20px rgba(31,169,113,0.28); }
  .ai-banner h4 { margin: 0 0 2px; font-size: 14px; color: #fff; }
  .ai-banner p { margin: 0; font-size: 11px; color: rgba(255,255,255,0.88); }
  .ai-btn { background: #fff; color: var(--accent-dark); border: none; padding: 10px 14px; border-radius: 12px; font-weight: 800; font-size: 12px; cursor: pointer; white-space: nowrap; transition: transform 0.15s ease, opacity 0.15s ease; }
  .ai-btn:active { transform: scale(0.95); }

  .water-card { background: #EAF3FF; border: 1px solid #D5E6FB; color: var(--text); border-radius: 20px; padding: 15px 18px; margin-bottom: 14px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; }
  .water-info h4 { margin: 0; font-size: 13px; color: var(--blue); }
  .water-info .w-val { font-size: 16px; font-weight: 800; margin-top: 2px; }
  .water-glasses { display: flex; gap: 4px; font-size: 16px; margin-top: 5px; flex-wrap: wrap; }
  .water-btns { display: flex; gap: 6px; }
  .water-btns button { background: #fff; color: var(--blue); border: 1px solid #D5E6FB; padding: 8px 12px; border-radius: 12px; font-weight: 800; font-size: 12px; cursor: pointer; transition: transform 0.15s ease; }
  .water-btns button:active { transform: scale(0.93); }

  .meal-card { background: var(--card); border: 1px solid var(--border); color: var(--text); border-radius: 20px; padding: 16px; margin-bottom: 12px; box-shadow: var(--shadow); }
  .meal-header { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--input); padding-bottom: 10px; margin-bottom: 8px; }
  .meal-title { font-size: 15px; font-weight: 800; display: flex; align-items: center; gap: 6px; }
  .meal-kcal { font-size: 12px; color: var(--muted); font-weight: 700; margin-top: 2px; }
  .meal-add-btn { background: var(--accent-soft); color: var(--accent-dark); border: none; border-radius: 12px; padding: 8px 13px; font-weight: 800; font-size: 12px; cursor: pointer; transition: transform 0.15s ease; }
  .meal-add-btn:active { transform: scale(0.95); }

  .food-item { display: flex; justify-content: space-between; align-items: center; padding: 8px 0; border-bottom: 1px solid var(--input); font-size: 13px; transition: opacity 0.2s ease; gap: 8px; }
  .food-item:last-child { border-bottom: none; }
  .food-name { font-weight: 600; }
  .food-meta { font-size: 11px; color: var(--muted); margin-top: 1px; }
  .food-right { display: flex; align-items: center; gap: 8px; }
  .food-cal { font-weight: 800; color: var(--text); font-size: 13px; white-space: nowrap; }
  .btn-group { display: flex; gap: 4px; }
  .action-btn { background: var(--input); color: var(--accent-dark); border: none; border-radius: 8px; padding: 5px 8px; font-size: 11px; font-weight: 700; cursor: pointer; transition: transform 0.1s ease; }
  .action-btn:active { transform: scale(0.9); }
  .del-btn { background: #FDECEC; color: var(--warn); border: none; border-radius: 8px; width: 26px; height: 26px; font-weight: 800; cursor: pointer; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }

  .tab-view { display: none; opacity: 0; transform: translateY(8px); transition: opacity 0.3s ease, transform 0.3s ease; }
  .tab-view.active { display: block; opacity: 1; transform: translateY(0); }

  .box-card { background: var(--card); border: 1px solid var(--border); color: var(--text); border-radius: 20px; padding: 18px; margin-bottom: 14px; box-shadow: var(--shadow); }
  .box-card h3 { margin: 0 0 10px; color: var(--text); font-size: 16px; font-weight: 800; }

  input, select { width: 100%; padding: 12px; margin: 6px 0; border-radius: 12px; border: 1.5px solid transparent; background: var(--input); color: var(--text); font-size: 14px; outline: none; transition: border-color 0.2s ease, background 0.2s ease; }
  input::placeholder { color: #9AA9A0; }
  input:focus, select:focus { border-color: var(--accent); background: #fff; }
  input[type=checkbox] { width: auto; margin: 0 4px 0 0; vertical-align: middle; accent-color: var(--accent); }

  .btn-main { width: 100%; padding: 13px; margin-top: 10px; border-radius: 14px; border: none; background: var(--accent); color: var(--on-accent); font-weight: 800; font-size: 14px; cursor: pointer; transition: transform 0.15s ease, opacity 0.15s ease; box-shadow: 0 6px 14px rgba(31,169,113,0.25); }
  .btn-main:active { transform: scale(0.97); opacity: 0.92; }
  .btn-danger { background: var(--warn); color: #fff; box-shadow: 0 6px 14px rgba(229,72,77,0.22); }

  .row-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
  .row-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; }

  .shop-item { display: flex; justify-content: space-between; align-items: center; padding: 10px 0; border-bottom: 1px solid var(--input); font-size: 13px; cursor: pointer; transition: opacity 0.2s ease; }
  .shop-item.checked { opacity: 0.4; text-decoration: line-through; }

  .bottom-nav { position: fixed; bottom: 0; left: 0; right: 0; background: rgba(255,255,255,0.95); border-top: 1px solid var(--border); display: flex; justify-content: space-around; padding: 8px 4px calc(10px + env(safe-area-inset-bottom)); z-index: 100; backdrop-filter: blur(12px); }
  .nav-btn { background: transparent; border: none; color: #9AA9A0; font-size: 10px; font-weight: 700; display: flex; flex-direction: column; align-items: center; gap: 3px; cursor: pointer; width: 20%; transition: color 0.2s ease; }
  .nav-btn .ico { width: 44px; height: 28px; border-radius: 14px; display: flex; align-items: center; justify-content: center; transition: background 0.2s ease; }
  .nav-btn .ico svg { width: 22px; height: 22px; stroke: currentColor; fill: none; stroke-width: 1.9; stroke-linecap: round; stroke-linejoin: round; }
  .nav-btn.active { color: var(--accent-dark); }
  .nav-btn.active .ico { background: var(--accent-soft); }

  canvas { width: 100%; height: 190px; background: var(--card2); border-radius: 12px; margin-top: 8px; display: block; }
  table { width: 100%; border-collapse: collapse; margin-top: 8px; font-size: 12px; }
  th, td { text-align: left; padding: 8px 6px; border-bottom: 1px solid var(--input); }
  th { color: var(--muted); font-weight: 700; }

  .timer-box { text-align: center; }
  .timer-time { font-size: 54px; font-weight: 900; color: var(--accent-dark); font-variant-numeric: tabular-nums; line-height: 1.1; transition: color 0.2s ease; }
  .timer-time.done { color: var(--warn); }
  .timer-sub { font-size: 11px; color: var(--muted); min-height: 14px; }
  .chip-row { display: flex; gap: 6px; justify-content: center; margin: 10px 0 4px; flex-wrap: wrap; }
  .chip { background: var(--input); color: var(--muted); border: none; border-radius: 14px; padding: 8px 14px; font-weight: 800; font-size: 12px; cursor: pointer; }
  .chip.active { color: #fff; background: var(--accent); }
  .btn-row { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
  .btn-alt { background: var(--accent-soft); color: var(--accent-dark); border: none; box-shadow: none; }
  .auto-row { display: flex; align-items: center; justify-content: center; font-size: 12px; color: var(--muted); margin-top: 10px; }
  .hint { font-size: 11px; color: var(--muted); min-height: 15px; margin: 2px 2px 4px; }
  .pr-toast { font-size: 13px; font-weight: 800; color: var(--amber); text-align: center; min-height: 18px; margin-top: 8px; }
  .ex-group { margin-bottom: 14px; }
  .ex-title { font-weight: 800; font-size: 14px; display: flex; justify-content: space-between; margin-bottom: 4px; }
  .ex-title span:last-child { font-size: 11px; color: var(--muted); font-weight: 600; }
</style>
</head>
<body>

<div class="onboard-backdrop" id="onboardModal">
  <div class="onboard-card">
    <h2>🎯 Добро пожаловать в Grammika!</h2>
    <p>Настроим ваши персональные цели для автоподбора рациона за 30 секунд.</p>

    <div class="row-2">
      <div>
        <label style="font-size:11px; color:var(--muted)">Возраст (лет)</label>
        <input type="number" id="obAge" value="29">
      </div>
      <div>
        <label style="font-size:11px; color:var(--muted)">Рост (см)</label>
        <input type="number" id="obHeight" value="167">
      </div>
    </div>
    <div class="row-2">
      <div>
        <label style="font-size:11px; color:var(--muted)">Текущий вес (кг)</label>
        <input type="number" step="0.1" id="obWeight" value="93">
      </div>
      <div>
        <label style="font-size:11px; color:var(--muted)">Целевой вес (кг)</label>
        <input type="number" step="0.1" id="obTarget" value="73">
      </div>
    </div>
    <label style="font-size:11px; color:var(--muted)">Активность</label>
    <select id="obAct">
      <option value="1.2">Сидячая работа за ПК (1.2)</option>
      <option value="1.375" selected>Прогулки / легкая актив. (1.375)</option>
      <option value="1.55">Тренировки 3-5 раз в нед. (1.55)</option>
    </select>

    <label style="font-size:11px; color:var(--muted); margin-top:10px; display:block;">🚫 Что исключить из авто-меню:</label>
    <div style="display:flex; gap:12px; margin-top:4px; font-size:13px; flex-wrap:wrap;">
      <label><input type="checkbox" value="fish" id="excFish"> Рыбу</label>
      <label><input type="checkbox" value="meat" id="excMeat"> Мясо/Говядину</label>
      <label><input type="checkbox" value="dairy" id="excDairy"> Молочку/Творог</label>
    </div>

    <button class="btn-main" style="margin-top:16px;" onclick="completeOnboarding()">🚀 Создать мой план питания</button>
  </div>
</div>

<div class="container">
  <div class="top-header">
    <div class="brand-title"><span class="logo-dot">G</span>GRAMMIKA <span id="streakBadge" style="font-size:11px; background:var(--input); color:var(--accent); border:1px solid var(--border); padding:3px 9px; border-radius:12px;">🔥 Стрик: 0 дн.</span></div>
    <div class="date-pill">
      <span>📅</span>
      <input type="date" id="currentDate" onchange="refresh()">
    </div>
  </div>

  <div class="wk-strip" id="weekStrip"></div>

  <!-- ВКЛАДКА 1: ДНЕВНИК -->
  <div id="tab-diary" class="tab-view active">
    <div class="fitia-hero">
      <div class="cal-summary">
        <div class="cal-stat">
          <div class="num" id="eatenCal">0</div>
          <div class="lbl">Съедено ккал</div>
        </div>
        <div class="ring-box active" id="calRing">
          <div class="ring-val" id="remCal">1850</div>
          <div class="ring-lbl" id="remLbl">Осталось</div>
        </div>
        <div class="cal-stat">
          <div class="num" id="burnedCal" style="color:var(--warn)">🔥 0</div>
          <div class="lbl">Сожжено ккал</div>
        </div>
      </div>

      <div class="macros-row">
        <div class="macro-card">
          <div class="macro-head"><span>Белки</span><span style="color:var(--blue)" id="pPct">0%</span></div>
          <div class="p-bar-bg"><div class="p-bar-fill" id="pBar" style="background:var(--blue)"></div></div>
          <div class="macro-sub" id="pText">0 / 138 г</div>
        </div>
        <div class="macro-card">
          <div class="macro-head"><span>Жиры</span><span style="color:var(--amber)" id="fPct">0%</span></div>
          <div class="p-bar-bg"><div class="p-bar-fill" id="fBar" style="background:var(--amber)"></div></div>
          <div class="macro-sub" id="fText">0 / 62 г</div>
        </div>
        <div class="macro-card">
          <div class="macro-head"><span>Углеводы</span><span style="color:var(--green)" id="cPct">0%</span></div>
          <div class="p-bar-bg"><div class="p-bar-fill" id="cBar" style="background:var(--green)"></div></div>
          <div class="macro-sub" id="cText">0 / 185 г</div>
        </div>
      </div>
    </div>

    <div class="ai-banner">
      <div>
        <h4>✨ Автогенератор меню Grammika</h4>
        <p>Сборка идеального рациона с учетом ваших исключений</p>
      </div>
      <button class="ai-btn" onclick="generateSmartDay()">Собрать день</button>
    </div>

    <div class="water-card">
      <div class="water-info">
        <h4>💧 Водный баланс</h4>
        <div class="w-val" id="waterText">0 / 2250 мл</div>
        <div class="water-glasses" id="waterGlasses"></div>
      </div>
      <div class="water-btns">
        <button onclick="addWater(-250)">-250</button>
        <button onclick="addWater(250)">+250 мл 💧</button>
      </div>
    </div>

    <div id="mealsContainer"></div>
  </div>

  <!-- ВКЛАДКА 2: АВТО-МЕНЮ И СПИСОК ПОКУПОК -->
  <div id="tab-plan" class="tab-view">
    <div class="box-card">
      <h3>✨ Умный подбор рациона</h3>
      <p style="font-size:12px; color:var(--muted); margin-top:0;">
        Алгоритм распределяет норму калорий на Завтрак (25%), Обед (35%), Ужин (25%) и Перекус (15%), автоматически исключая нелюбимые продукты.
      </p>
      <button class="btn-main" onclick="generateSmartDay()">🔄 Перегенерировать рацион</button>
      <button class="btn-main btn-danger" style="margin-top:8px;" onclick="clearDayMeals()">🗑️ Очистить рацион за этот день</button>
    </div>

    <div class="box-card">
      <h3>🛒 Список покупок на сегодня (чек-лист)</h3>
      <p style="font-size:11px; color:var(--muted); margin-top:0;">Нажмите на продукт, чтобы отметить его как купленный.</p>
      <div id="shoppingList" style="margin-top:8px;"></div>
    </div>
  </div>

  <!-- ВКЛАДКА 3: СИЛОВЫЕ ТРЕНИРОВКИ -->
  <div id="tab-strength" class="tab-view">
    <div class="box-card timer-box">
      <h3>⏱ Таймер отдыха</h3>
      <div class="timer-time" id="restTime">01:30</div>
      <div class="timer-sub" id="restSub">Выберите время и нажмите «Старт»</div>
      <div class="chip-row" id="restChips">
        <button class="chip" data-sec="60" onclick="setRest(60)">1:00</button>
        <button class="chip active" data-sec="90" onclick="setRest(90)">1:30</button>
        <button class="chip" data-sec="120" onclick="setRest(120)">2:00</button>
        <button class="chip" data-sec="180" onclick="setRest(180)">3:00</button>
      </div>
      <div class="btn-row">
        <button class="btn-main" onclick="startRest()">▶ Старт</button>
        <button class="btn-main btn-alt" onclick="stopRest(true)">⏹ Сброс</button>
      </div>
      <div class="auto-row"><label><input type="checkbox" id="autoRest" checked> Запускать после каждого подхода</label></div>
    </div>

    <div class="box-card">
      <h3>🏋️ Записать подход</h3>
      <input type="text" id="exName" list="exList" placeholder="Упражнение" autocomplete="off" oninput="onExerciseInput(false)" onchange="onExerciseInput(true)">
      <datalist id="exList"></datalist>
      <div class="hint" id="exHint"></div>
      <div class="row-2">
        <input type="number" step="0.5" inputmode="decimal" id="setWeight" placeholder="Вес, кг (0 = свой)">
        <input type="number" inputmode="numeric" id="setReps" placeholder="Повторения">
      </div>
      <button class="btn-main" onclick="submitSet()">+ Записать подход</button>
      <div class="pr-toast" id="prToast"></div>
    </div>

    <div class="box-card">
      <h3>📋 Тренировка за день</h3>
      <div id="strSummary" style="font-size:12px; color:var(--muted); margin-bottom:10px;"></div>
      <div id="strengthList"></div>
    </div>
  </div>

  <!-- ВКЛАДКА 4: ВЕС И ШАГИ -->
  <div id="tab-progress" class="tab-view">
    <div class="box-card">
      <h3>⚖️ Утренний вес и шаги</h3>
      <div id="weightSummary" style="font-size:13px; color:var(--muted); margin-bottom:8px;"></div>
      <div class="row-2">
        <input type="number" step="0.1" id="wInput" placeholder="Вес (кг)">
        <input type="number" id="sInput" placeholder="Шаги">
      </div>
      <input type="text" id="noteInput" placeholder="Заметка / самочувствие">
      <button class="btn-main" onclick="sendEntry()">Сохранить отметку</button>
    </div>

    <div class="box-card">
      <h3>🏃 Кардио / активность</h3>
      <select id="workSelect" onchange="onWorkSelect()"></select>
      <input type="text" id="wkName" placeholder="Название активности">
      <div class="row-2">
        <input type="number" id="wkMin" placeholder="Минуты" value="30" oninput="onWorkSelect()">
        <input type="number" id="wkCal" placeholder="Сожжено ккал">
      </div>
      <button class="btn-main" onclick="sendWork()">+ Записать активность</button>
      <div id="workoutList" style="margin-top:10px;"></div>
    </div>

    <div class="box-card">
      <h3>📈 График динамики веса</h3>
      <canvas id="weightChart"></canvas>
    </div>

    <div class="box-card">
      <h3>📋 История</h3>
      <table id="historyTable">
        <thead><tr><th>Дата</th><th>Вес</th><th>Ккал</th><th>Шаги</th><th>Вода</th><th></th></tr></thead>
        <tbody></tbody>
      </table>
    </div>
  </div>

  <!-- ВКЛАДКА 5: ПРОФИЛЬ, РЕЦЕПТЫ И БЭКАП -->
  <div id="tab-profile" class="tab-view">
    <div class="box-card">
      <h3>🍲 Конструктор своих рецептов</h3>
      <p style="font-size:12px; color:var(--muted); margin-top:0;">Добавьте свое блюдо (на 100 г продукта), и оно появится в общем каталоге.</p>
      <input type="text" id="customName" placeholder="Название блюда / рецепта">
      <div class="row-2">
        <input type="number" id="customCal" placeholder="Ккал (на 100г)">
        <input type="number" step="0.1" id="customP" placeholder="Белки (на 100г)">
      </div>
      <div class="row-2">
        <input type="number" step="0.1" id="customF" placeholder="Жиры (на 100г)">
        <input type="number" step="0.1" id="customC" placeholder="Углеводы (на 100г)">
      </div>
      <button class="btn-main" onclick="saveCustomFood()">+ Сохранить рецепт</button>
    </div>

    <div class="box-card">
      <h3>🎯 Мои цели и настройки</h3>
      <div class="row-2">
        <div>
          <label style="font-size:11px; color:var(--muted)">Текущий вес (кг)</label>
          <input type="number" step="0.1" id="cWeight" value="93">
        </div>
        <div>
          <label style="font-size:11px; color:var(--muted)">Цель (кг)</label>
          <input type="number" step="0.1" id="cTargetWeight" value="73">
        </div>
      </div>
      <div class="row-2" style="margin-top:6px;">
        <div>
          <label style="font-size:11px; color:var(--muted)">Норма ккал</label>
          <input type="number" id="gCal">
        </div>
        <div>
          <label style="font-size:11px; color:var(--muted)">Вода (мл)</label>
          <input type="number" id="gWater">
        </div>
      </div>
      <button class="btn-main" onclick="saveManualGoals()">Сохранить цели</button>
      <button class="btn-main" style="background:var(--accent-soft); color:var(--accent-dark); box-shadow:none; margin-top:8px;" onclick="openOnboardAgain()">🔄 Повторить опросник / онбординг</button>
    </div>

    <div class="box-card">
      <h3>💾 Резервное копирование данных</h3>
      <button class="btn-main" style="background:var(--blue); color:#fff; box-shadow:0 6px 14px rgba(59,130,246,0.25);" onclick="exportBackup()">📥 Скачать бэкап данных</button>
      <button class="btn-main" style="background:var(--input); color:var(--text); box-shadow:none; margin-top:8px;" onclick="document.getElementById('importFile').click()">📤 Восстановить из бэкапа</button>
      <input type="file" id="importFile" style="display:none" accept=".json" onchange="importBackup(event)">
    </div>
  </div>
</div>

<!-- Модальное окно еды -->
<div class="modal-backdrop" id="mealModal">
  <div class="modal-card">
    <div class="modal-top">
      <h3 id="modalTitle">Добавить в рацион</h3>
      <button class="close-btn" onclick="closeMealModal()">✕</button>
    </div>
    <label style="font-size:11px; color:var(--muted)">Выберите из базы (включая ваши рецепты):</label>
    <select id="foodSelect" onchange="onFoodSelect()"></select>
    <input type="text" id="fName" placeholder="Название продукта">
    <div class="row-2">
      <div>
        <label style="font-size:11px; color:var(--muted)">Граммы</label>
        <input type="number" id="fGrams" value="150" oninput="onFoodSelect()">
      </div>
      <div>
        <label style="font-size:11px; color:var(--muted)">Калории</label>
        <input type="number" id="fCal" placeholder="Ккал">
      </div>
    </div>
    <div class="row-3">
      <input type="number" step="0.1" id="fP" placeholder="Белки">
      <input type="number" step="0.1" id="fF" placeholder="Жиры">
      <input type="number" step="0.1" id="fC" placeholder="Углеводы">
    </div>
    <button class="btn-main" onclick="submitMeal()">+ Записать</button>
  </div>
</div>

<!-- Модальное окно замены продукта -->
<div class="modal-backdrop" id="replaceModal">
  <div class="modal-card">
    <div class="modal-top">
      <h3>🔄 Замена продукта</h3>
      <button class="close-btn" onclick="closeReplaceModal()">✕</button>
    </div>
    <p style="font-size:12px; color:var(--muted); margin-top:0;">Выберите продукт для замены:</p>
    <select id="replaceSelect"></select>
    <button class="btn-main" onclick="confirmReplace()">Заменить в рационе</button>
  </div>
</div>

<div class="bottom-nav">
  <button class="nav-btn active" onclick="switchTab('diary', this)"><span class="ico"><svg viewBox="0 0 24 24"><path d="M7 3v8M4.5 3v5a2.5 2.5 0 0 0 5 0V3M7 11v10"/><path d="M17 21V3c-2.3 1.3-3.5 3.9-3.5 7v2.5H17"/></svg></span><span>Дневник</span></button>
  <button class="nav-btn" onclick="switchTab('plan', this)"><span class="ico"><svg viewBox="0 0 24 24"><path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/><path d="M18.5 16.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z"/></svg></span><span>Авто-меню</span></button>
  <button class="nav-btn" onclick="switchTab('strength', this)"><span class="ico"><svg viewBox="0 0 24 24"><path d="M6.5 6.5v11M17.5 6.5v11M3.5 9.5v5M20.5 9.5v5M6.5 12h11"/></svg></span><span>Силовая</span></button>
  <button class="nav-btn" onclick="switchTab('progress', this)"><span class="ico"><svg viewBox="0 0 24 24"><path d="M3 17l6-6 4 4 8-8"/><path d="M15 7h6v6"/></svg></span><span>Вес</span></button>
  <button class="nav-btn" onclick="switchTab('profile', this)"><span class="ico"><svg viewBox="0 0 24 24"><circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 3.6-7 8-7s8 3 8 7"/></svg></span><span>Профиль</span></button>
</div>

<script>
  const tg = (window.Telegram && window.Telegram.WebApp) ? window.Telegram.WebApp : null;
  if (tg) {
    tg.expand();
    try { tg.setHeaderColor('#F3F6F4'); tg.setBackgroundColor('#F3F6F4'); if (tg.setBottomBarColor) tg.setBottomBarColor('#FFFFFF'); } catch (e) {}
  }

  const initData = (tg && tg.initData) ? tg.initData : "";
  let tgUserId = "default_user";
  if (tg && tg.initDataUnsafe && tg.initDataUnsafe.user) {
    const u = tg.initDataUnsafe.user;
    tgUserId = String(u.id || "default_user");
  }

  // Дата по ЛОКАЛЬНОМУ времени устройства (toISOString даёт UTC и сдвигает день).
  function localDate(d) {
    d = d || new Date();
    const p = n => String(n).padStart(2, '0');
    return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate());
  }
  function esc(s) {
    return String(s === null || s === undefined ? '' : s).replace(/[&<>"']/g, c => (
      {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]
    ));
  }
  function fmt(n) { return String(+Number(n).toFixed(1)); }

  const todayStr = localDate();
  document.getElementById('currentDate').value = todayStr;

  let foodCatalog = [];
  let workCatalog = [];
  let activeMealType = 'breakfast';
  let activeReplaceMealId = null;
  let lastEntries = [];
  let lastTarget = 0;
  let strengthLast = {};
  let strengthPR = {};

  const MEAL_META = [
    { key: 'breakfast', title: '🌅 Завтрак', pct: 25 },
    { key: 'lunch',     title: '☀️ Обед',    pct: 35 },
    { key: 'dinner',    title: '🌙 Ужин',    pct: 25 },
    { key: 'snack',     title: '🍏 Перекус', pct: 15 }
  ];

  function currentDate() {
    return document.getElementById('currentDate').value || localDate();
  }

  function renderWeekStrip() {
    const cur = new Date(currentDate() + 'T00:00:00');
    const dow = (cur.getDay() + 6) % 7;
    const start = new Date(cur);
    start.setDate(cur.getDate() - dow);
    const names = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
    const sel = currentDate();
    let html = '';
    for (let i = 0; i < 7; i++) {
      const d = new Date(start);
      d.setDate(start.getDate() + i);
      const ds = localDate(d);
      html += `<button class="wk-day${ds === sel ? ' sel' : ''}${ds === todayStr ? ' today' : ''}" onclick="pickDate('${ds}')"><span>${names[i]}</span><b>${d.getDate()}</b></button>`;
    }
    document.getElementById('weekStrip').innerHTML = html;
  }

  function pickDate(ds) {
    document.getElementById('currentDate').value = ds;
    refresh();
  }

  function switchTab(tabName, btn) {
    document.getElementById('weekStrip').style.display =
      (tabName === 'diary' || tabName === 'strength') ? '' : 'none';
    document.querySelectorAll('.tab-view').forEach(el => el.classList.remove('active'));
    document.getElementById('tab-' + tabName).classList.add('active');
    document.querySelectorAll('.nav-btn').forEach(el => el.classList.remove('active'));
    if (btn) btn.classList.add('active');
    if (tabName === 'progress') drawWeightChart(lastEntries, lastTarget);
  }

  async function apiFetch(url, options) {
    options = options || {};
    options.headers = options.headers || {};
    options.headers['X-Telegram-Init-Data'] = initData;
    options.headers['X-Fallback-User'] = tgUserId;
    const res = await fetch(url, options);
    let body = null;
    try { body = await res.json(); } catch (e) {}
    if (!res.ok) {
      throw new Error((body && body.error) || ('Ошибка сервера ' + res.status));
    }
    return body;
  }

  window.addEventListener('unhandledrejection', ev => {
    ev.preventDefault();
    alert((ev.reason && ev.reason.message) || 'Что-то пошло не так');
  });

  function post(url, payload) {
    return apiFetch(url, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload)
    });
  }

  async function completeOnboarding() {
    const age = parseFloat(document.getElementById('obAge').value || 29);
    const h = parseFloat(document.getElementById('obHeight').value || 167);
    const w = parseFloat(document.getElementById('obWeight').value || 93);
    const tw = parseFloat(document.getElementById('obTarget').value || 73);
    const act = parseFloat(document.getElementById('obAct').value || 1.375);

    const excluded = [];
    if (document.getElementById('excFish').checked) excluded.push('fish');
    if (document.getElementById('excMeat').checked) excluded.push('meat');
    if (document.getElementById('excDairy').checked) excluded.push('dairy');

    const bmr = (10 * w) + (6.25 * h) - (5 * age) + 5;
    const tdee = bmr * act;
    const targetCal = Math.round(tdee * 0.80);
    const targetP = Math.round((targetCal * 0.30) / 4);
    const targetF = Math.round((targetCal * 0.30) / 9);
    const targetC = Math.round((targetCal * 0.40) / 4);
    const targetWater = Math.round(w * 25);

    await post('/api/save_goals', {
      onboarded: "1",
      target_weight: tw,
      target_cal: targetCal,
      target_p: targetP,
      target_f: targetF,
      target_c: targetC,
      target_water: targetWater,
      excluded: excluded
    });
    document.getElementById('onboardModal').classList.remove('open');
    refresh();
  }

  function openOnboardAgain() {
    document.getElementById('onboardModal').classList.add('open');
  }

  async function saveCustomFood() {
    const name = document.getElementById('customName').value.trim();
    const cal = parseFloat(document.getElementById('customCal').value || 0);
    const p = parseFloat(document.getElementById('customP').value || 0);
    const f = parseFloat(document.getElementById('customF').value || 0);
    const c = parseFloat(document.getElementById('customC').value || 0);
    if (!name) return alert('Введите название рецепта!');

    await post('/api/add_custom_food', {name, cal, p, f, c});
    alert('Рецепт успешно сохранен в ваш каталог!');
    ['customName', 'customCal', 'customP', 'customF', 'customC'].forEach(id => {
      document.getElementById(id).value = '';
    });
    refresh();
  }

  async function exportBackup() {
    const data = await apiFetch('/api/export_backup');
    const blob = new Blob([JSON.stringify(data, null, 2)], {type: 'application/json'});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `grammika_backup_${localDate()}.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function importBackup(event) {
    const file = event.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = async function(e) {
      let jsonContent;
      try {
        jsonContent = JSON.parse(e.target.result);
      } catch (err) {
        alert('Неверный формат JSON файла.');
        return;
      }
      if (!confirm('Текущие данные будут заменены данными из бэкапа. Продолжить?')) return;
      await post('/api/import_backup', jsonContent);
      alert('Резервная копия успешно восстановлена!');
      refresh();
    };
    reader.readAsText(file);
    event.target.value = '';
  }

  function openMealModal(mealType, title) {
    activeMealType = mealType;
    document.getElementById('modalTitle').innerText = title;
    document.getElementById('foodSelect').value = '';
    document.getElementById('fName').value = '';
    document.getElementById('fGrams').value = '150';
    document.getElementById('fCal').value = '';
    document.getElementById('fP').value = '';
    document.getElementById('fF').value = '';
    document.getElementById('fC').value = '';
    document.getElementById('mealModal').classList.add('open');
  }

  function closeMealModal() {
    document.getElementById('mealModal').classList.remove('open');
  }

  function openReplaceModal(mealId) {
    activeReplaceMealId = mealId;
    const rSel = document.getElementById('replaceSelect');
    rSel.innerHTML = foodCatalog.map((f, i) => `<option value="${i}">${esc(f.name)} (${esc(f.cal)} ккал/100г)</option>`).join('');
    document.getElementById('replaceModal').classList.add('open');
  }

  function closeReplaceModal() {
    document.getElementById('replaceModal').classList.remove('open');
  }

  async function confirmReplace() {
    const idx = document.getElementById('replaceSelect').value;
    if (idx === "" || !foodCatalog[idx] || !activeReplaceMealId) return;
    const newFood = foodCatalog[idx];
    await post('/api/replace_meal', {
      id: activeReplaceMealId, date: currentDate(), new_name: newFood.name,
      cal100: newFood.cal, p100: newFood.p, f100: newFood.f, c100: newFood.c
    });
    closeReplaceModal();
    refresh();
  }

  function onFoodSelect() {
    const idx = document.getElementById('foodSelect').value;
    const g = parseFloat(document.getElementById('fGrams').value || 100);
    if (idx !== "" && foodCatalog[idx]) {
      const f = foodCatalog[idx];
      const fac = g / 100.0;
      document.getElementById('fName').value = f.name;
      document.getElementById('fCal').value = Math.round(f.cal * fac);
      document.getElementById('fP').value = (f.p * fac).toFixed(1);
      document.getElementById('fF').value = (f.f * fac).toFixed(1);
      document.getElementById('fC').value = (f.c * fac).toFixed(1);
    }
  }

  function onWorkSelect() {
    const idx = document.getElementById('workSelect').value;
    const mins = parseFloat(document.getElementById('wkMin').value || 30);
    if (idx !== "" && workCatalog[idx]) {
      const w = workCatalog[idx];
      document.getElementById('wkName').value = w.name;
      document.getElementById('wkCal').value = Math.round(w.cal_min * mins);
    }
  }

  // ------------------------------------------------------------ график веса
  function drawWeightChart(entries, target) {
    const cv = document.getElementById('weightChart');
    const w = cv.clientWidth, h = cv.clientHeight;
    if (!w || !h) return;
    const dpr = window.devicePixelRatio || 1;
    cv.width = Math.round(w * dpr);
    cv.height = Math.round(h * dpr);
    const ctx = cv.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);

    const css = getComputedStyle(document.documentElement);
    const cAccent = css.getPropertyValue('--accent').trim();
    const cMuted = css.getPropertyValue('--muted').trim();
    const cBorder = css.getPropertyValue('--border').trim();
    const cAmber = css.getPropertyValue('--amber').trim();

    let pts = (entries || []).filter(e => e.weight > 0).map(e => ({
      t: new Date(e.date + 'T00:00:00').getTime(), v: e.weight, date: e.date
    }));
    ctx.font = '11px system-ui, sans-serif';
    if (pts.length < 2) {
      ctx.fillStyle = cMuted;
      ctx.textAlign = 'center';
      ctx.fillText('Нужно минимум 2 записи веса', w / 2, h / 2);
      return;
    }
    pts = pts.slice(-60);
    const tgt = Number(target) > 0 ? Number(target) : null;

    const vals = pts.map(p => p.v);
    if (tgt) vals.push(tgt);
    let min = Math.min.apply(null, vals), max = Math.max.apply(null, vals);
    const pad = Math.max((max - min) * 0.15, 0.5);
    min -= pad; max += pad;

    const L = 40, R = 12, T = 14, B = 26;
    const t0 = pts[0].t, t1 = pts[pts.length - 1].t;
    const X = t => L + ((t - t0) / ((t1 - t0) || 1)) * (w - L - R);
    const Y = v => T + ((max - v) / (max - min)) * (h - T - B);

    // сетка и подписи по оси Y
    ctx.strokeStyle = cBorder; ctx.lineWidth = 1;
    ctx.fillStyle = cMuted; ctx.textAlign = 'right';
    for (let i = 0; i <= 4; i++) {
      const v = min + (max - min) * (i / 4);
      const y = Y(v);
      ctx.beginPath(); ctx.moveTo(L, y); ctx.lineTo(w - R, y); ctx.stroke();
      ctx.fillText(v.toFixed(1), L - 6, y + 4);
    }

    // линия цели
    if (tgt) {
      ctx.save();
      ctx.strokeStyle = cAmber; ctx.setLineDash([5, 4]);
      ctx.beginPath(); ctx.moveTo(L, Y(tgt)); ctx.lineTo(w - R, Y(tgt)); ctx.stroke();
      ctx.restore();
      ctx.fillStyle = cAmber; ctx.textAlign = 'left';
      ctx.fillText('цель ' + tgt, L + 4, Y(tgt) - 4);
    }

    // линия веса
    ctx.strokeStyle = cAccent; ctx.lineWidth = 2.5; ctx.lineJoin = 'round';
    ctx.beginPath();
    pts.forEach((p, i) => { if (i === 0) ctx.moveTo(X(p.t), Y(p.v)); else ctx.lineTo(X(p.t), Y(p.v)); });
    ctx.stroke();
    ctx.fillStyle = cAccent;
    pts.forEach(p => { ctx.beginPath(); ctx.arc(X(p.t), Y(p.v), 3, 0, Math.PI * 2); ctx.fill(); });

    // последняя точка и даты
    const last = pts[pts.length - 1];
    ctx.textAlign = 'right';
    ctx.fillText(last.v.toFixed(1), Math.min(X(last.t) + 2, w - R), Y(last.v) - 8);
    ctx.fillStyle = cMuted;
    const ddmm = s => s.slice(8, 10) + '.' + s.slice(5, 7);
    ctx.textAlign = 'left';  ctx.fillText(ddmm(pts[0].date), L, h - 8);
    ctx.textAlign = 'right'; ctx.fillText(ddmm(last.date), w - R, h - 8);
  }
  window.addEventListener('resize', () => drawWeightChart(lastEntries, lastTarget));

  // ------------------------------------------------------------ таймер отдыха
  let restLen = 90, restEnd = 0, restTimer = null;

  function fmtTime(sec) {
    const m = Math.floor(sec / 60), s = sec % 60;
    return String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
  }
  function setRest(sec) {
    restLen = sec;
    document.querySelectorAll('#restChips .chip').forEach(c => {
      c.classList.toggle('active', Number(c.dataset.sec) === sec);
    });
    if (!restTimer) {
      document.getElementById('restTime').innerText = fmtTime(sec);
      document.getElementById('restTime').classList.remove('done');
    }
  }
  function startRest() {
    if (restTimer) clearInterval(restTimer);
    restEnd = Date.now() + restLen * 1000;
    document.getElementById('restTime').classList.remove('done');
    document.getElementById('restSub').innerText = 'Отдыхаем…';
    restTimer = setInterval(restTick, 250);
    restTick();
  }
  function restTick() {
    const left = Math.max(0, Math.ceil((restEnd - Date.now()) / 1000));
    document.getElementById('restTime').innerText = fmtTime(left);
    if (left <= 0) { stopRest(false); restDone(); }
  }
  function stopRest(reset) {
    if (restTimer) { clearInterval(restTimer); restTimer = null; }
    if (reset) {
      document.getElementById('restTime').innerText = fmtTime(restLen);
      document.getElementById('restTime').classList.remove('done');
      document.getElementById('restSub').innerText = 'Выберите время и нажмите «Старт»';
    }
  }
  function restDone() {
    document.getElementById('restTime').classList.add('done');
    document.getElementById('restSub').innerText = 'Отдых закончен — следующий подход!';
    try { if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred('success'); } catch (e) {}
    try { if (navigator.vibrate) navigator.vibrate([250, 100, 250]); } catch (e) {}
    try {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (AC) {
        const ac = new AC(), osc = ac.createOscillator(), gain = ac.createGain();
        osc.frequency.value = 880; gain.gain.value = 0.15;
        osc.connect(gain); gain.connect(ac.destination);
        osc.start(); osc.stop(ac.currentTime + 0.35);
      }
    } catch (e) {}
  }

  // ------------------------------------------------------------ силовые
  function onExerciseInput(prefill) {
    const name = document.getElementById('exName').value.trim();
    const last = strengthLast[name];
    const pr = strengthPR[name];
    const parts = [];
    if (last) parts.push(`Прошлый раз: ${last.weight > 0 ? fmt(last.weight) + ' кг' : 'свой вес'} × ${last.reps}`);
    if (pr > 0) parts.push(`Рекорд: ${fmt(pr)} кг`);
    document.getElementById('exHint').innerText = parts.join(' • ');
    if (prefill && last) {
      const wEl = document.getElementById('setWeight'), rEl = document.getElementById('setReps');
      if (!wEl.value) wEl.value = last.weight;
      if (!rEl.value) rEl.value = last.reps;
    }
  }

  async function submitSet() {
    const name = document.getElementById('exName').value.trim();
    const weight = parseFloat((document.getElementById('setWeight').value || '0').replace(',', '.')) || 0;
    const reps = parseInt(document.getElementById('setReps').value || '0');
    if (!name) return alert('Выберите или введите упражнение');
    if (!(reps > 0)) return alert('Укажите число повторений');
    const res = await post('/api/add_set', {date: currentDate(), exercise: name, weight, reps});
    const toast = document.getElementById('prToast');
    toast.innerText = res.pr ? '🏆 Новый рекорд по весу!' : '';
    if (res.pr) { try { if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred('success'); } catch (e) {} }
    if (document.getElementById('autoRest').checked) startRest();
    refresh();
  }

  async function deleteSet(id) {
    await post('/api/delete_set', {id});
    refresh();
  }

  function renderStrength(d) {
    strengthLast = d.last_sets || {};
    strengthPR = d.prs || {};
    document.getElementById('exList').innerHTML =
      (d.exercises || []).map(n => `<option value="${esc(n)}"></option>`).join('');
    onExerciseInput(false);

    const sets = d.day_sets || [];
    let vol = 0;
    sets.forEach(s => { vol += s.weight * s.reps; });
    document.getElementById('strSummary').innerText = sets.length
      ? `Подходов: ${sets.length} • Тоннаж: ${Math.round(vol)} кг`
      : 'В этот день подходов пока нет';

    const groups = {}, order = [];
    sets.forEach(s => {
      if (!groups[s.exercise]) { groups[s.exercise] = []; order.push(s.exercise); }
      groups[s.exercise].push(s);
    });
    document.getElementById('strengthList').innerHTML = order.map(name => {
      const list = groups[name];
      let v = 0; list.forEach(s => { v += s.weight * s.reps; });
      const rows = list.map((s, i) => `
        <div class="food-item">
          <span>${i + 1}. <b>${s.weight > 0 ? esc(fmt(s.weight)) + ' кг' : 'свой вес'}</b> × ${esc(s.reps)}</span>
          <button class="del-btn" onclick="deleteSet(${Number(s.id)})">✕</button>
        </div>`).join('');
      return `<div class="ex-group">
        <div class="ex-title"><span>${esc(name)}</span><span>${list.length} подх. • ${Math.round(v)} кг</span></div>
        ${rows}
      </div>`;
    }).join('');
  }

  // ------------------------------------------------------------ основной рендер
  async function refresh() {
    const date = currentDate();
    const d = await apiFetch('/api/state?date=' + encodeURIComponent(date) + '&today=' + encodeURIComponent(localDate()));

    if (d.settings.onboarded !== "1") {
      document.getElementById('onboardModal').classList.add('open');
    }

    if (d.foods) {
      foodCatalog = d.foods;
      document.getElementById('foodSelect').innerHTML =
        '<option value="">-- Выберите продукт или свой рецепт --</option>' +
        foodCatalog.map((f, i) => `<option value="${i}">${esc(f.name)} (${esc(f.cal)} ккал/100г)</option>`).join('');
    }
    if (workCatalog.length === 0 && d.workouts) {
      workCatalog = d.workouts;
      document.getElementById('workSelect').innerHTML =
        '<option value="">-- Выберите тренировку --</option>' +
        workCatalog.map((w, i) => `<option value="${i}">${esc(w.name)} (${esc(w.cal_min)} ккал/мин)</option>`).join('');
    }

    const s = d.settings;
    document.getElementById('gCal').value = s.target_cal;
    document.getElementById('gWater').value = s.target_water;
    document.getElementById('cTargetWeight').value = s.target_weight;
    if (s.target_weight) {
      document.getElementById('obTarget').value = s.target_weight;
    }
    document.getElementById('streakBadge').innerText = `🔥 Стрик: ${d.streak || 0} дн.`;

    const eaten = d.today_cal;
    const burned = d.today_burn;
    const targetCal = s.target_cal;
    const rem = targetCal - eaten;

    document.getElementById('eatenCal').innerText = eaten;
    document.getElementById('burnedCal').innerText = '🔥 ' + burned;

    const ring = document.getElementById('calRing');
    if (rem >= 0) {
      document.getElementById('remCal').innerText = rem;
      document.getElementById('remLbl').innerText = 'Осталось ккал';
      ring.className = 'ring-box active';
    } else {
      document.getElementById('remCal').innerText = '+' + Math.abs(rem);
      document.getElementById('remLbl').innerText = 'Профицит ккал';
      ring.className = 'ring-box over';
    }
    ring.style.setProperty('--p', rem < 0 ? 100 : (targetCal > 0 ? Math.min(100, (eaten / targetCal) * 100) : 0));
    if (rem < 0 && eaten <= targetCal * 1.05) ring.className = 'ring-box active';
    renderWeekStrip();

    updateMacroBar('p', d.today_p, s.target_p);
    updateMacroBar('f', d.today_f, s.target_f);
    updateMacroBar('c', d.today_c, s.target_c);

    const wMl = d.water_ml || 0;
    document.getElementById('waterText').innerText = `${wMl} / ${s.target_water} мл`;
    const totalGlasses = Math.max(Math.round(s.target_water / 250), 6);
    const filledGlasses = Math.floor(wMl / 250);
    let gHtml = '';
    for (let i = 0; i < totalGlasses; i++) {
      gHtml += i < filledGlasses ? '🥛' : '⚪';
    }
    document.getElementById('waterGlasses').innerHTML = gHtml;

    const mealsByType = { breakfast: [], lunch: [], dinner: [], snack: [] };
    (d.day_meals || []).forEach(m => {
      const k = mealsByType[m.meal_type] ? m.meal_type : 'breakfast';
      mealsByType[k].push(m);
    });

    let mealsHtml = '';
    MEAL_META.forEach(meta => {
      const list = mealsByType[meta.key];
      const sumCal = Math.round(list.reduce((a, b) => a + b.calories, 0));
      const recCal = Math.round((targetCal * meta.pct) / 100);

      let itemsHtml = '';
      if (list.length === 0) {
        itemsHtml = `<div style="font-size:12px; color:var(--muted); padding:4px 0;">Пока пусто • Рекомендуется ~${recCal} ккал</div>`;
      } else {
        list.forEach(item => {
          itemsHtml += `
            <div class="food-item">
              <div>
                <div class="food-name">${esc(item.food_name)}</div>
                <div class="food-meta">${Math.round(item.grams)} г • Б:${item.p.toFixed(1)} Ж:${item.f.toFixed(1)} У:${item.c.toFixed(1)}</div>
              </div>
              <div class="food-right">
                <span class="food-cal">${Math.round(item.calories)} ккал</span>
                <div class="btn-group">
                  <button class="action-btn" onclick="openReplaceModal(${Number(item.id)})">🔄</button>
                  <button class="del-btn" onclick="deleteMeal(${Number(item.id)})">✕</button>
                </div>
              </div>
            </div>`;
        });
      }

      mealsHtml += `
        <div class="meal-card">
          <div class="meal-header">
            <div>
              <div class="meal-title">${meta.title}</div>
              <div class="meal-kcal">${sumCal} / ${recCal} ккал</div>
            </div>
            <button class="meal-add-btn" onclick="openMealModal('${meta.key}', '${meta.title}')">+ Добавить</button>
          </div>
          <div>${itemsHtml}</div>
        </div>`;
    });
    document.getElementById('mealsContainer').innerHTML = mealsHtml;

    const shopDiv = document.getElementById('shoppingList');
    if ((d.day_meals || []).length === 0) {
      shopDiv.innerHTML = '<span style="color:var(--muted)">Сгенерируйте меню на день, чтобы сформировать чек-лист.</span>';
    } else {
      const totals = {};
      d.day_meals.forEach(m => { totals[m.food_name] = (totals[m.food_name] || 0) + m.grams; });
      shopDiv.innerHTML = Object.entries(totals).map(([name, gr]) =>
        `<div class="shop-item" onclick="this.classList.toggle('checked')">
          <span>☑️ ${esc(name)}</span><b style="color:var(--accent)">${Math.round(gr)} г</b>
        </div>`
      ).join('');
    }

    renderStrength(d);

    document.getElementById('weightSummary').innerText = `Текущий: ${d.curr_weight} | ${d.weight_sub}`;
    document.getElementById('workoutList').innerHTML = (d.day_workouts || []).map(w =>
      `<div class="food-item">
        <span>🏃 ${esc(w.name)} (${Math.round(w.mins)} мин)</span>
        <div class="food-right">
          <b style="color:var(--warn)">🔥 ${Math.round(w.cal)} ккал</b>
          <button class="del-btn" onclick="deleteWorkout(${Number(w.id)})">✕</button>
        </div>
      </div>`
    ).join('');

    document.querySelector('#historyTable tbody').innerHTML =
      (d.entries || []).slice().reverse().slice(0, 12).map(e => `<tr>
        <td>${esc(e.date)}</td>
        <td><b>${e.weight > 0 ? esc(e.weight) + ' кг' : '-'}</b></td>
        <td>${esc(e.calories)}</td>
        <td>${esc(e.steps)}</td>
        <td>${esc(e.water_ml || 0)} мл</td>
        <td><button class="del-btn" data-date="${esc(e.date)}" onclick="deleteEntry(this.dataset.date)">✕</button></td>
      </tr>`).join('');

    lastEntries = d.entries || [];
    lastTarget = s.target_weight;
    drawWeightChart(lastEntries, lastTarget);
  }

  function updateMacroBar(prefix, curr, target) {
    const pct = target > 0 ? Math.min(Math.round((curr / target) * 100), 100) : 0;
    document.getElementById(prefix + 'Pct').innerText = pct + '%';
    document.getElementById(prefix + 'Bar').style.width = pct + '%';
    document.getElementById(prefix + 'Text').innerText = `${Math.round(curr)} / ${target} г`;
  }

  async function submitMeal() {
    const name = document.getElementById('fName').value.trim();
    const grams = parseFloat(document.getElementById('fGrams').value || 100);
    const cal = parseFloat(document.getElementById('fCal').value || 0);
    const p = parseFloat(document.getElementById('fP').value || 0);
    const f = parseFloat(document.getElementById('fF').value || 0);
    const c = parseFloat(document.getElementById('fC').value || 0);
    if (!name) return;

    await post('/api/add_meal', {date: currentDate(), meal_type: activeMealType, name, grams, cal, p, f, c});
    closeMealModal();
    refresh();
  }

  async function deleteMeal(id) {
    await post('/api/delete_meal', {id, date: currentDate()});
    refresh();
  }

  async function generateSmartDay() {
    await post('/api/generate_day', {date: currentDate()});
    refresh();
  }

  async function clearDayMeals() {
    await post('/api/clear_day_meals', {date: currentDate()});
    refresh();
  }

  async function addWater(delta) {
    await post('/api/add_water', {date: currentDate(), delta});
    refresh();
  }

  async function sendEntry() {
    const weight = parseFloat((document.getElementById('wInput').value || '0').replace(',', '.')) || 0;
    const steps = parseInt(document.getElementById('sInput').value || '0') || 0;
    const note = document.getElementById('noteInput').value.trim();
    await post('/api/add_entry', {date: currentDate(), weight, steps, note});
    document.getElementById('wInput').value = '';
    document.getElementById('sInput').value = '';
    document.getElementById('noteInput').value = '';
    refresh();
  }

  async function deleteEntry(date) {
    await post('/api/delete_entry', {date});
    refresh();
  }

  async function sendWork() {
    const name = document.getElementById('wkName').value.trim();
    const mins = parseFloat(document.getElementById('wkMin').value || 30);
    const cal = parseFloat(document.getElementById('wkCal').value || 100);
    if (!name) return;
    await post('/api/add_workout', {date: currentDate(), name, mins, cal});
    document.getElementById('wkName').value = '';
    document.getElementById('workSelect').value = '';
    refresh();
  }

  async function deleteWorkout(id) {
    await post('/api/delete_workout', {id});
    refresh();
  }

  async function saveManualGoals() {
    await post('/api/save_goals', {
      target_weight: parseFloat(document.getElementById('cTargetWeight').value || 73),
      target_cal: parseInt(document.getElementById('gCal').value || 1850),
      target_water: parseInt(document.getElementById('gWater').value || 2250)
    });
    await refresh();
    switchTab('diary', document.querySelectorAll('.nav-btn')[0]);
  }

  refresh();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------- API logic

def handle_post(cur, user_id, path, data):
    """Выполняет POST-запрос. Возвращает dict с доп. полями ответа или None, если путь неизвестен."""

    if path == "/api/import_backup":
        sections = {}
        for key in ("settings", "entries", "meals", "workouts", "custom_foods", "strength_sets"):
            v = data.get(key, [])
            if not isinstance(v, list):
                raise ValueError("bad backup section")
            sections[key] = v

        for table in ("settings", "entries", "meal_log", "workout_log", "custom_foods", "strength_sets"):
            cur.execute(f"DELETE FROM {table} WHERE telegram_id = %s", (user_id,))

        for k, v in sections["settings"]:
            if k in SETTINGS_KEYS:
                if k == "excluded" and isinstance(v, str):
                    v = json.loads(v)
                cur.execute(
                    "INSERT INTO settings (telegram_id, key, value) VALUES (%s, %s, %s) "
                    "ON CONFLICT (telegram_id, key) DO UPDATE SET value = excluded.value",
                    (user_id, k, clean_setting(k, v)),
                )
        for r in sections["entries"]:
            cur.execute(
                """INSERT INTO entries
                   (telegram_id, date, weight, calories, steps, note, water_ml)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (telegram_id, date) DO UPDATE SET
                       weight = excluded.weight, calories = excluded.calories,
                       steps = excluded.steps, note = excluded.note,
                       water_ml = excluded.water_ml""",
                (user_id, need_date(r[0]), num(r[1] or 0, 0, 500), int(num(r[2] or 0, 0, 100000)),
                 int(num(r[3] or 0, 0, 500000)), str(r[4] or "")[:500], int(num(r[5] or 0, 0, 100000))),
            )
        for r in sections["meals"]:
            cur.execute(
                """INSERT INTO meal_log
                   (telegram_id, date, meal_type, food_name, grams, calories, protein, fat, carbs)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (user_id, need_date(r[0]), r[1] if r[1] in MEAL_TYPES else "breakfast",
                 need_text(r[2]), num(r[3] or 0, 0, 5000), num(r[4] or 0, 0, 20000),
                 num(r[5] or 0, 0, 2000), num(r[6] or 0, 0, 2000), num(r[7] or 0, 0, 2000)),
            )
        for r in sections["workouts"]:
            cur.execute(
                """INSERT INTO workout_log
                   (telegram_id, date, workout_name, duration_min, calories_burned)
                   VALUES (%s, %s, %s, %s, %s)""",
                (user_id, need_date(r[0]), need_text(r[1]), num(r[2] or 0, 0, 1440), num(r[3] or 0, 0, 10000)),
            )
        for r in sections["custom_foods"]:
            cur.execute(
                """INSERT INTO custom_foods (telegram_id, name, calories, protein, fat, carbs)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (user_id, need_text(r[0]), num(r[1] or 0, 0, 2000), num(r[2] or 0, 0, 100),
                 num(r[3] or 0, 0, 100), num(r[4] or 0, 0, 100)),
            )
        for r in sections["strength_sets"]:
            cur.execute(
                """INSERT INTO strength_sets (telegram_id, date, exercise, weight, reps)
                   VALUES (%s, %s, %s, %s, %s)""",
                (user_id, need_date(r[0]), need_text(r[1]), num(r[2], 0, 1000), int(num(r[3], 1, 1000))),
            )
        return {}

    if path == "/api/add_custom_food":
        cur.execute(
            """INSERT INTO custom_foods (telegram_id, name, calories, protein, fat, carbs)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (user_id, need_text(data["name"]), num(data["cal"], 0, 2000), num(data["p"], 0, 100),
             num(data["f"], 0, 100), num(data["c"], 0, 100)),
        )
        return {}

    if path == "/api/replace_meal":
        meal_id = int(data["id"])
        date = need_date(data["date"])
        new_name = need_text(data["new_name"])
        cal100 = num(data["cal100"], 0, 2000)
        p100 = num(data["p100"], 0, 100)
        f100 = num(data["f100"], 0, 100)
        c100 = num(data["c100"], 0, 100)
        cur.execute(
            "SELECT grams FROM meal_log WHERE id = %s AND telegram_id = %s", (meal_id, user_id)
        )
        mrow = cur.fetchone()
        if mrow:
            fac = mrow[0] / 100.0
            cur.execute(
                """UPDATE meal_log SET food_name = %s, calories = %s, protein = %s, fat = %s, carbs = %s
                   WHERE id = %s AND telegram_id = %s""",
                (new_name, round(cal100 * fac, 1), round(p100 * fac, 1),
                 round(f100 * fac, 1), round(c100 * fac, 1), meal_id, user_id),
            )
            sync_daily_calories(cur, user_id, date)
        return {}

    if path == "/api/save_goals":
        for k, v in data.items():
            if k not in SETTINGS_KEYS:
                continue
            cur.execute(
                """INSERT INTO settings (telegram_id, key, value) VALUES (%s, %s, %s)
                   ON CONFLICT(telegram_id, key) DO UPDATE SET value = excluded.value""",
                (user_id, k, clean_setting(k, v)),
            )
        return {}

    if path == "/api/add_water":
        date = need_date(data["date"])
        delta = int(num(data.get("delta", 250), -5000, 5000))
        cur.execute(
            "SELECT water_ml FROM entries WHERE telegram_id = %s AND date = %s", (user_id, date)
        )
        row = cur.fetchone()
        curr_w = row[0] if (row and row[0]) else 0
        new_w = max(0, curr_w + delta)
        cur.execute(
            """INSERT INTO entries (telegram_id, date, weight, calories, steps, note, water_ml)
               VALUES (%s, %s, 0, 0, 0, '', %s)
               ON CONFLICT(telegram_id, date) DO UPDATE SET water_ml = excluded.water_ml""",
            (user_id, date, new_w),
        )
        return {}

    if path == "/api/add_meal":
        date = need_date(data["date"])
        meal_type = data.get("meal_type", "breakfast")
        if meal_type not in MEAL_TYPES:
            raise ValueError("bad meal type")
        cur.execute(
            """INSERT INTO meal_log
               (telegram_id, date, meal_type, food_name, grams, calories, protein, fat, carbs)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (user_id, date, meal_type, need_text(data["name"]), num(data["grams"], 0, 5000),
             num(data["cal"], 0, 20000), num(data.get("p", 0), 0, 2000),
             num(data.get("f", 0), 0, 2000), num(data.get("c", 0), 0, 2000)),
        )
        sync_daily_calories(cur, user_id, date)
        return {}

    if path == "/api/delete_meal":
        date = need_date(data["date"])
        cur.execute(
            "DELETE FROM meal_log WHERE id = %s AND telegram_id = %s", (int(data["id"]), user_id)
        )
        sync_daily_calories(cur, user_id, date)
        return {}

    if path == "/api/clear_day_meals":
        date = need_date(data["date"])
        cur.execute("DELETE FROM meal_log WHERE telegram_id = %s AND date = %s", (user_id, date))
        sync_daily_calories(cur, user_id, date)
        return {}

    if path == "/api/generate_day":
        date = need_date(data["date"])
        settings = get_user_settings(cur, user_id)
        target_cal = settings["target_cal"]
        excluded = settings["excluded"]

        filtered_foods = [f for f in DEFAULT_FOODS if f[5] not in excluded]
        if not filtered_foods:
            filtered_foods = DEFAULT_FOODS
        food_map = {f[0]: f for f in filtered_foods}

        cur.execute("DELETE FROM meal_log WHERE telegram_id = %s AND date = %s", (user_id, date))

        shares = {"breakfast": 0.25, "lunch": 0.35, "dinner": 0.25, "snack": 0.15}
        for m_type, m_share in shares.items():
            meal_cal_budget = target_cal * m_share
            valid_templates = [
                tpl for tpl in MEAL_TEMPLATES[m_type]
                if all(item[0] in food_map for item in tpl)
            ]
            if not valid_templates:
                valid_templates = MEAL_TEMPLATES[m_type]

            chosen_combo = random.choice(valid_templates)
            for f_name, item_share in chosen_combo:
                if f_name not in food_map:
                    avail_keys = list(food_map.keys())
                    if not avail_keys:
                        continue
                    f_name = random.choice(avail_keys)

                _, cal100, p100, f100, c100, _ = food_map[f_name]
                item_cal = meal_cal_budget * item_share
                grams = round((item_cal / cal100) * 100 / 10) * 10
                grams = max(grams, 40)
                fac = grams / 100.0
                cur.execute(
                    """INSERT INTO meal_log
                       (telegram_id, date, meal_type, food_name, grams, calories, protein, fat, carbs)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (user_id, date, m_type, f_name, grams, round(cal100 * fac, 1),
                     round(p100 * fac, 1), round(f100 * fac, 1), round(c100 * fac, 1)),
                )
        sync_daily_calories(cur, user_id, date)
        return {}

    if path == "/api/add_entry":
        date = need_date(data["date"])
        weight = num(data.get("weight", 0), 0, 500)
        steps = int(num(data.get("steps", 0), 0, 500000))
        note = str(data.get("note", ""))[:500]
        cur.execute(
            """INSERT INTO entries (telegram_id, date, weight, calories, steps, note, water_ml)
               VALUES (%s, %s, %s, %s, %s, %s, 0)
               ON CONFLICT(telegram_id, date) DO UPDATE SET
                   weight = CASE WHEN excluded.weight > 0 THEN excluded.weight ELSE entries.weight END,
                   steps = CASE WHEN excluded.steps > 0 THEN excluded.steps ELSE entries.steps END,
                   note = CASE WHEN excluded.note != '' THEN excluded.note ELSE entries.note END""",
            (user_id, date, weight, 0, steps, note),
        )
        return {}

    if path == "/api/delete_entry":
        cur.execute(
            "DELETE FROM entries WHERE telegram_id = %s AND date = %s",
            (user_id, need_date(data["date"])),
        )
        return {}

    if path == "/api/add_workout":
        cur.execute(
            """INSERT INTO workout_log (telegram_id, date, workout_name, duration_min, calories_burned)
               VALUES (%s, %s, %s, %s, %s)""",
            (user_id, need_date(data["date"]), need_text(data["name"]),
             num(data["mins"], 0, 1440), num(data["cal"], 0, 10000)),
        )
        return {}

    if path == "/api/delete_workout":
        cur.execute(
            "DELETE FROM workout_log WHERE id = %s AND telegram_id = %s", (int(data["id"]), user_id)
        )
        return {}

    if path == "/api/add_set":
        date = need_date(data["date"])
        exercise = need_text(data["exercise"])
        weight = num(data.get("weight", 0), 0, 1000)
        reps = int(num(data["reps"], 1, 1000))
        cur.execute(
            "SELECT MAX(weight) FROM strength_sets WHERE telegram_id = %s AND exercise = %s",
            (user_id, exercise),
        )
        prev = cur.fetchone()[0]
        is_pr = prev is not None and weight > prev and weight > 0
        cur.execute(
            """INSERT INTO strength_sets (telegram_id, date, exercise, weight, reps)
               VALUES (%s, %s, %s, %s, %s)""",
            (user_id, date, exercise, weight, reps),
        )
        return {"pr": bool(is_pr)}

    if path == "/api/delete_set":
        cur.execute(
            "DELETE FROM strength_sets WHERE id = %s AND telegram_id = %s", (int(data["id"]), user_id)
        )
        return {}

    return None


class NotFound(Exception):
    pass


DB_UNAVAILABLE = (psycopg.OperationalError, PoolTimeout)
BAD_INPUT = (KeyError, ValueError, TypeError, IndexError, psycopg.DataError)


class GrammikaAppHandler(http.server.BaseHTTPRequestHandler):

    def _get_uid(self):
        init_data = self.headers.get("X-Telegram-Init-Data", "")
        if BOT_TOKEN:
            uid = verify_init_data(init_data, BOT_TOKEN)
            if not uid:
                self._send_json(
                    {"error": "Не удалось подтвердить пользователя Telegram. Откройте приложение из Telegram заново."},
                    401,
                )
            return uid
        # Режим разработки: подпись не проверяется.
        return extract_user_id(init_data, self.headers.get("X-Fallback-User", "default_user"))

    def _send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _fail(self, exc, where):
        if isinstance(exc, DB_UNAVAILABLE):
            print(where, "- база недоступна:", repr(exc))
            self._send_json(
                {"error": "База данных временно недоступна. Повторите через несколько секунд."}, 503
            )
        else:
            print(where, "- ошибка:", repr(exc))
            self._send_json({"error": "Внутренняя ошибка сервера"}, 500)

    def do_GET(self):
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path

        if path in ("/", "/index.html"):
            body = WEB_APP_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path not in ("/api/state", "/api/export_backup"):
            self._send_json({"error": "not found"}, 404)
            return

        user_id = self._get_uid()
        if user_id is None:
            return
        try:
            if path == "/api/state":
                self._api_state(user_id, urllib.parse.parse_qs(parsed_url.query))
            else:
                self._api_export(user_id)
        except Exception as exc:
            self._fail(exc, "GET " + path)

    def _api_state(self, user_id, qs):
        now_str = datetime.now().strftime("%Y-%m-%d")
        date_str = qs.get("date", [now_str])[0]
        today_str = qs.get("today", [date_str])[0]
        if not valid_date(date_str) or not valid_date(today_str):
            self._send_json({"error": "Некорректная дата"}, 400)
            return

        with POOL.connection() as conn:
            with conn.cursor() as cur:
                settings = get_user_settings(cur, user_id)

                cur.execute(
                    "SELECT date, weight, calories, steps, note, water_ml FROM entries WHERE telegram_id = %s ORDER BY date ASC",
                    (user_id,),
                )
                entries = [
                    {"date": r[0], "weight": r[1] or 0, "calories": r[2] or 0, "steps": r[3] or 0,
                     "note": r[4] or "", "water_ml": r[5] or 0}
                    for r in cur.fetchall()
                ]

                streak = compute_streak(cur, user_id, today_str)

                cur.execute(
                    "SELECT water_ml FROM entries WHERE telegram_id = %s AND date = %s",
                    (user_id, date_str),
                )
                w_row = cur.fetchone()
                water_ml = w_row[0] if (w_row and w_row[0]) else 0

                cur.execute(
                    """SELECT id, meal_type, food_name, grams, calories, protein, fat, carbs
                       FROM meal_log WHERE telegram_id = %s AND date = %s ORDER BY id ASC""",
                    (user_id, date_str),
                )
                day_meals = [
                    {"id": r[0], "meal_type": r[1] or "breakfast", "food_name": r[2],
                     "grams": r[3] or 0, "calories": r[4] or 0,
                     "p": r[5] or 0, "f": r[6] or 0, "c": r[7] or 0}
                    for r in cur.fetchall()
                ]

                cur.execute(
                    """SELECT id, workout_name, duration_min, calories_burned
                       FROM workout_log WHERE telegram_id = %s AND date = %s ORDER BY id DESC""",
                    (user_id, date_str),
                )
                day_workouts = [
                    {"id": r[0], "name": r[1], "mins": r[2] or 0, "cal": r[3] or 0}
                    for r in cur.fetchall()
                ]

                cur.execute(
                    "SELECT name, calories, protein, fat, carbs FROM custom_foods WHERE telegram_id = %s ORDER BY id",
                    (user_id,),
                )
                custom_foods = [
                    {"name": r[0] + " (свой рецепт)", "cal": r[1] or 0, "p": r[2] or 0,
                     "f": r[3] or 0, "c": r[4] or 0, "cat": "custom"}
                    for r in cur.fetchall()
                ]

                # силовые тренировки
                cur.execute(
                    "SELECT id, exercise, weight, reps FROM strength_sets WHERE telegram_id = %s AND date = %s ORDER BY id ASC",
                    (user_id, date_str),
                )
                day_sets = [
                    {"id": r[0], "exercise": r[1], "weight": r[2] or 0, "reps": r[3]}
                    for r in cur.fetchall()
                ]
                cur.execute(
                    "SELECT DISTINCT exercise FROM strength_sets WHERE telegram_id = %s", (user_id,)
                )
                own_exercises = [r[0] for r in cur.fetchall()]
                exercises = sorted(set(DEFAULT_EXERCISES) | set(own_exercises))

                cur.execute(
                    "SELECT exercise, MAX(weight) FROM strength_sets WHERE telegram_id = %s GROUP BY exercise",
                    (user_id,),
                )
                prs = {r[0]: r[1] for r in cur.fetchall()}

                cur.execute(
                    """SELECT s.exercise, s.weight, s.reps FROM strength_sets s
                       JOIN (SELECT exercise, MAX(date) AS d FROM strength_sets
                             WHERE telegram_id = %s AND date < %s GROUP BY exercise) m
                         ON s.exercise = m.exercise AND s.date = m.d
                       WHERE s.telegram_id = %s""",
                    (user_id, date_str, user_id),
                )
                last_sets = {}
                for ex, wt, reps in cur.fetchall():
                    best = last_sets.get(ex)
                    if best is None or (wt, reps) > (best["weight"], best["reps"]):
                        last_sets[ex] = {"weight": wt, "reps": reps}

        all_foods = [
            {"name": f[0], "cal": f[1], "p": f[2], "f": f[3], "c": f[4], "cat": f[5]}
            for f in DEFAULT_FOODS
        ] + custom_foods

        valid = [e for e in entries if e["weight"] > 0]
        if valid:
            curr = valid[-1]["weight"]
            start = valid[0]["weight"]
            diff = curr - start
            sign = "+" if diff > 0 else ""
            curr_w = f"{curr:.1f} кг"
            rem_w = curr - settings["target_weight"]
            sub = f"Со старта: {sign}{diff:.1f} кг | До цели: {rem_w:.1f} кг"
        else:
            curr_w = "-- кг"
            sub = f"Цель: {settings['target_weight']} кг"

        self._send_json(
            {
                "settings": settings,
                "curr_weight": curr_w,
                "weight_sub": sub,
                "water_ml": water_ml,
                "streak": streak,
                "today_cal": int(round(sum(m["calories"] for m in day_meals))),
                "today_p": round(sum(m["p"] for m in day_meals), 1),
                "today_f": round(sum(m["f"] for m in day_meals), 1),
                "today_c": round(sum(m["c"] for m in day_meals), 1),
                "today_burn": int(round(sum(w["cal"] for w in day_workouts))),
                "day_meals": day_meals,
                "day_workouts": day_workouts,
                "entries": entries,
                "foods": all_foods,
                "workouts": [{"name": w[0], "cal_min": w[1]} for w in DEFAULT_WORKOUTS],
                "day_sets": day_sets,
                "exercises": exercises,
                "prs": prs,
                "last_sets": last_sets,
            }
        )

    def _api_export(self, user_id):
        with POOL.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT key, value FROM settings WHERE telegram_id = %s", (user_id,))
                settings = [list(r) for r in cur.fetchall()]
                cur.execute(
                    "SELECT date, weight, calories, steps, note, water_ml FROM entries WHERE telegram_id = %s ORDER BY date",
                    (user_id,),
                )
                entries = [list(r) for r in cur.fetchall()]
                cur.execute(
                    "SELECT date, meal_type, food_name, grams, calories, protein, fat, carbs FROM meal_log WHERE telegram_id = %s ORDER BY id",
                    (user_id,),
                )
                meals = [list(r) for r in cur.fetchall()]
                cur.execute(
                    "SELECT date, workout_name, duration_min, calories_burned FROM workout_log WHERE telegram_id = %s ORDER BY id",
                    (user_id,),
                )
                workouts = [list(r) for r in cur.fetchall()]
                cur.execute(
                    "SELECT name, calories, protein, fat, carbs FROM custom_foods WHERE telegram_id = %s ORDER BY id",
                    (user_id,),
                )
                custom = [list(r) for r in cur.fetchall()]
                cur.execute(
                    "SELECT date, exercise, weight, reps FROM strength_sets WHERE telegram_id = %s ORDER BY id",
                    (user_id,),
                )
                sets = [list(r) for r in cur.fetchall()]

        self._send_json(
            {
                "settings": settings,
                "entries": entries,
                "meals": meals,
                "workouts": workouts,
                "custom_foods": custom,
                "strength_sets": sets,
            }
        )

    def do_POST(self):
        user_id = self._get_uid()
        if user_id is None:
            return
        path = urllib.parse.urlparse(self.path).path

        try:
            length = int(self.headers.get("Content-Length", 0))
            if length > MAX_BODY:
                self._send_json({"error": "Слишком большой запрос"}, 413)
                return
            raw_body = self.rfile.read(length).decode("utf-8") if length else ""
            data = json.loads(raw_body) if raw_body else {}
            if not isinstance(data, dict):
                raise ValueError("body must be an object")
        except (ValueError, UnicodeDecodeError):
            self._send_json({"error": "Некорректный запрос"}, 400)
            return

        try:
            # Выход из блока with без ошибки = commit, любое исключение = rollback.
            with POOL.connection() as conn:
                with conn.cursor() as cur:
                    result = handle_post(cur, user_id, path, data)
                if result is None:
                    raise NotFound()
        except NotFound:
            self._send_json({"error": "not found"}, 404)
            return
        except BAD_INPUT:
            self._send_json({"error": "Некорректные данные"}, 400)
            return
        except Exception as exc:
            self._fail(exc, "POST " + path)
            return

        out = {"ok": True}
        out.update(result)
        self._send_json(out)

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    open_pool()
    init_db()
    if not BOT_TOKEN:
        print(
            "ВНИМАНИЕ: BOT_TOKEN не задан — подпись Telegram не проверяется (режим разработки). "
            "Перед публикацией задайте переменную окружения BOT_TOKEN."
        )
    server = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), GrammikaAppHandler)
    print(f"Grammika Server running on port {PORT}")
    server.serve_forever()
