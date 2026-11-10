"""
Общая база продуктов Grammika: меню сетей + Open Food Facts.

Подключается в server.py:
    import food_db
    food_db.setup(POOL)                     # после open_pool() и init_db()
    food_db.search(POOL, q, brand)          # из GET /api/foods_search

Как растёт база: если по запросу в своей таблице мало результатов, сервер спрашивает
Open Food Facts и сохраняет найденное. Меню сетей берётся из папки restaurants/*.json
и перечитывается раз в сутки (позиции, удалённые из файла, пропадают из поиска).
"""
import glob
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
RESTAURANTS_DIR = os.path.join(HERE, "restaurants")
REFRESH_HOURS = float(os.environ.get("FOODS_REFRESH_HOURS", "24"))
OFF_UA = os.environ.get("OFF_USER_AGENT", "").strip() or "Grammika/1.1 (Telegram Mini App)"
OFF_SEARCH = "https://world.openfoodfacts.org/cgi/search.pl"
OFF_FIELDS = "code,product_name,product_name_ru,brands,nutriments,serving_quantity"
MIN_LOCAL = 8            # меньше стольких своих результатов — спрашиваем Open Food Facts
OFF_PER_MINUTE = 8       # у Open Food Facts лимит 10 поисков в минуту
QUERY_TTL_DAYS = 30      # один и тот же запрос в OFF повторно не чаще раза в месяц

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS foods (
        id BIGSERIAL PRIMARY KEY,
        source TEXT NOT NULL,
        source_id TEXT NOT NULL,
        name TEXT NOT NULL,
        brand TEXT,
        kcal DOUBLE PRECISION,
        protein DOUBLE PRECISION,
        fat DOUBLE PRECISION,
        carbs DOUBLE PRECISION,
        serving_g DOUBLE PRECISION,
        serving_name TEXT,
        barcode TEXT,
        status TEXT NOT NULL DEFAULT 'approved',
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (source, source_id)
    )""",
    "CREATE INDEX IF NOT EXISTS foods_brand ON foods (brand)",
    "CREATE INDEX IF NOT EXISTS foods_name_lower ON foods (lower(name))",
    """CREATE TABLE IF NOT EXISTS foods_queries (
        q TEXT PRIMARY KEY,
        fetched_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
]

# Народные названия сетей -> как сеть записана в базе
BRAND_ALIASES = {
    "кфс": "Rostic's", "kfc": "Rostic's", "ростикс": "Rostic's", "ростик": "Rostic's",
    "ростикс кфс": "Rostic's", "rostics": "Rostic's", "rostic's": "Rostic's",
    "макдональдс": "Вкусно — и точка", "макдоналдс": "Вкусно — и точка",
    "макдак": "Вкусно — и точка", "мак": "Вкусно — и точка", "mcdonalds": "Вкусно — и точка",
    "вкусно и точка": "Вкусно — и точка", "вкусно точка": "Вкусно — и точка",
    "вкусноиточка": "Вкусно — и точка",
    "бк": "Бургер Кинг", "бургер кинг": "Бургер Кинг", "burger king": "Бургер Кинг",
}

_off_calls = []
_off_lock = threading.Lock()
_started = False


def _norm(s):
    return re.sub(r"\s+", " ", str(s or "").lower().replace("ё", "е").replace("—", " ")
                  .replace("-", " ")).strip()


def parse_query(q):
    """'кфс стрипсы' -> ('стрипсы', "Rostic's")."""
    text = _norm(q)
    for alias in sorted(BRAND_ALIASES, key=len, reverse=True):
        a = _norm(alias)
        m = re.search(rf"(^|\s){re.escape(a)}(\s|$)", text)
        if m:
            return (text[:m.start()] + " " + text[m.end():]).strip(), BRAND_ALIASES[alias]
    return text, None


# ---------------------------------------------------------------- запись

UPSERT = """
INSERT INTO foods (source, source_id, name, brand, kcal, protein, fat, carbs,
                   serving_g, serving_name, barcode, status, updated_at)
VALUES (%(source)s, %(source_id)s, %(name)s, %(brand)s, %(kcal)s, %(protein)s, %(fat)s,
        %(carbs)s, %(serving_g)s, %(serving_name)s, %(barcode)s, 'approved', now())
ON CONFLICT (source, source_id) DO UPDATE SET
    name = EXCLUDED.name, brand = EXCLUDED.brand, kcal = EXCLUDED.kcal,
    protein = EXCLUDED.protein, fat = EXCLUDED.fat, carbs = EXCLUDED.carbs,
    serving_g = EXCLUDED.serving_g, serving_name = EXCLUDED.serving_name,
    barcode = EXCLUDED.barcode, status = 'approved', updated_at = now()
"""
_EMPTY = {"brand": None, "protein": None, "fat": None, "carbs": None, "serving_g": None,
          "serving_name": None, "barcode": None}


def _upsert(cur, foods):
    if foods:
        cur.executemany(UPSERT, [{**_EMPTY, **f} for f in foods])


# ---------------------------------------------------------------- сети

def _slug(s):
    return re.sub(r"[^a-zа-я0-9]+", "-", str(s).lower()).strip("-")


def _r1(v):
    return None if v is None else round(float(v), 1)


def load_restaurants(pool):
    total = 0
    for path in sorted(glob.glob(os.path.join(RESTAURANTS_DIR, "*.json"))):
        if os.path.basename(path).startswith("_"):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            chain = data["chain"]
            foods, ids = [], []
            for it in data["items"]:
                w = it.get("weight_g")
                if not w or it.get("kcal") is None:
                    continue
                sid = f"{_slug(chain)}:{it.get('id') or _slug(it['name'])}"
                if sid in ids:
                    continue
                ids.append(sid)
                k = 1 if it.get("per100") else 100.0 / float(w)  # приводим всё к 100 г
                foods.append({
                    "source": "restaurant", "source_id": sid, "name": it["name"], "brand": chain,
                    "kcal": _r1(it["kcal"] * k),
                    "protein": _r1(it["protein"] * k) if it.get("protein") is not None else None,
                    "fat": _r1(it["fat"] * k) if it.get("fat") is not None else None,
                    "carbs": _r1(it["carbs"] * k) if it.get("carbs") is not None else None,
                    "serving_g": float(w), "serving_name": it.get("serving_name") or "1 порция",
                })
            with pool.connection() as conn:
                with conn.cursor() as cur:
                    _upsert(cur, foods)
                    cur.execute("UPDATE foods SET status = 'archived' WHERE source = 'restaurant' "
                                "AND brand = %s AND NOT (source_id = ANY(%s))", (chain, ids))
            total += len(foods)
            print(f"Меню [{chain}]: загружено позиций {len(foods)}", flush=True)
        except Exception as exc:
            print(f"Меню {os.path.basename(path)}: ошибка {exc!r}", flush=True)
    return total


# ---------------------------------------------------------------- Open Food Facts

def _num(v):
    try:
        return round(float(v), 1)
    except (TypeError, ValueError):
        return None


def _parse_off(p):
    n = p.get("nutriments") or {}
    kcal = _num(n.get("energy-kcal_100g"))
    if kcal is None and _num(n.get("energy_100g")) is not None:
        kcal = round(_num(n.get("energy_100g")) / 4.184, 1)
    name = (p.get("product_name_ru") or p.get("product_name") or "").strip()
    code = str(p.get("code") or "").strip()
    if not name or not code or kcal is None or kcal > 950:
        return None
    if not re.search(r"[а-яА-ЯёЁ]", name):  # в приложении на русском — берём русские названия
        return None
    brand = (p.get("brands") or "").split(",")[0].strip() or None
    return {"source": "off", "source_id": code, "barcode": code, "name": name[:100],
            "brand": brand[:80] if brand else None, "kcal": kcal,
            "protein": _num(n.get("proteins_100g")), "fat": _num(n.get("fat_100g")),
            "carbs": _num(n.get("carbohydrates_100g")),
            "serving_g": _num(p.get("serving_quantity"))}


def _off_allowed():
    with _off_lock:
        now = time.time()
        while _off_calls and now - _off_calls[0] > 60:
            _off_calls.pop(0)
        if len(_off_calls) >= OFF_PER_MINUTE:
            return False
        _off_calls.append(now)
        return True


def off_search(q):
    params = {"search_terms": q, "search_simple": 1, "action": "process", "json": 1,
              "page_size": 40, "fields": OFF_FIELDS, "lc": "ru",
              "tagtype_0": "countries", "tag_contains_0": "contains", "tag_0": "russia"}
    req = urllib.request.Request(OFF_SEARCH + "?" + urllib.parse.urlencode(params),
                                 headers={"User-Agent": OFF_UA})
    with urllib.request.urlopen(req, timeout=8) as r:
        data = json.load(r)
    out, seen = [], set()
    for p in data.get("products", []):
        f = _parse_off(p)
        if f and f["source_id"] not in seen:
            seen.add(f["source_id"])
            out.append(f)
    return out


def _fetch_off_if_needed(pool, q):
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM foods_queries WHERE q = %s AND fetched_at > now() - make_interval(days => %s)",
            (q, QUERY_TTL_DAYS)).fetchone()
    if row or not _off_allowed():
        return False
    try:
        foods = off_search(q)
    except Exception as exc:
        print("Open Food Facts недоступен:", type(exc).__name__, flush=True)
        return False
    with pool.connection() as conn:
        with conn.cursor() as cur:
            _upsert(cur, foods)
            cur.execute("INSERT INTO foods_queries (q) VALUES (%s) "
                        "ON CONFLICT (q) DO UPDATE SET fetched_at = now()", (q,))
    return bool(foods)


# ---------------------------------------------------------------- поиск

SEARCH_SQL = """
SELECT name, brand, kcal, protein, fat, carbs, serving_g, serving_name, source
FROM foods
WHERE status = 'approved'
  AND (%(brand)s::text IS NULL OR brand = %(brand)s)
  AND (%(n)s = 0 OR (
        translate(lower(name), 'ё', 'е') LIKE %(w0)s
    AND translate(lower(name), 'ё', 'е') LIKE %(w1)s
    AND translate(lower(name), 'ё', 'е') LIKE %(w2)s
    AND translate(lower(name), 'ё', 'е') LIKE %(w3)s))
ORDER BY (translate(lower(name), 'ё', 'е') LIKE %(start)s) DESC,
         (source = 'restaurant') DESC, length(name), name
LIMIT %(lim)s
"""


def _local(pool, words, brand, limit):
    w = [f"%{x}%" for x in words[:4]] + ["%"] * (4 - min(len(words), 4))
    params = {"brand": brand, "n": len(words), "w0": w[0], "w1": w[1], "w2": w[2], "w3": w[3],
              "start": (words[0] + "%") if words else "%", "lim": limit}
    with pool.connection() as conn:
        rows = conn.execute(SEARCH_SQL, params).fetchall()
    return [{"name": r[0], "brand": r[1], "cal": r[2], "p": r[3] or 0, "f": r[4] or 0,
             "c": r[5] or 0, "g": round(r[6]) if r[6] else 100, "portion": r[7], "src": r[8]}
            for r in rows]


def search(pool, q="", brand=None, limit=40):
    text, alias_brand = parse_query(q)
    brand = (brand or alias_brand or None)
    if brand:
        brand = str(brand)[:80]
    words = [x for x in re.split(r"\s+", text.replace("%", "").replace("_", "")) if x][:4]
    if not words and not brand:
        return []
    if not words:  # просмотр меню сети целиком
        return _local(pool, [], brand, 300)
    items = _local(pool, words, brand, limit)
    if len(items) < MIN_LOCAL and not brand and len(text) >= 3:
        if _fetch_off_if_needed(pool, text):
            items = _local(pool, words, brand, limit)
    return items


def chains(pool):
    with pool.connection() as conn:
        rows = conn.execute("SELECT brand, count(*) FROM foods WHERE source = 'restaurant' "
                            "AND status = 'approved' GROUP BY brand ORDER BY brand").fetchall()
    return [{"name": r[0], "count": r[1]} for r in rows]


# ---------------------------------------------------------------- запуск

def setup(pool):
    """Создаёт таблицы и запускает фоновое обновление меню сетей (раз в сутки)."""
    global _started
    with pool.connection() as conn:
        for stmt in SCHEMA:
            conn.execute(stmt)
    if _started:
        return
    _started = True

    def loop():
        while True:
            try:
                load_restaurants(pool)
            except Exception as exc:
                print("Обновление меню сетей не удалось:", repr(exc), flush=True)
            time.sleep(REFRESH_HOURS * 3600)

    threading.Thread(target=loop, daemon=True, name="foods-refresh").start()
