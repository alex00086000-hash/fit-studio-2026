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
import html
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
RESTAURANTS_DIR = os.path.join(HERE, "restaurants")
REFRESH_HOURS = float(os.environ.get("FOODS_REFRESH_HOURS", "24"))
OFF_UA = os.environ.get("OFF_USER_AGENT", "").strip() or "Grammika/1.1 (Telegram Mini App)"
OFF_SEARCH = "https://world.openfoodfacts.org/cgi/search.pl"
OFF_FIELDS = "code,product_name,product_name_ru,brands,nutriments,serving_quantity,images,image_front_small_url"
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
    "ALTER TABLE foods ADD COLUMN IF NOT EXISTS image_url TEXT",
    "CREATE INDEX IF NOT EXISTS foods_name_lower ON foods (lower(name))",
    """CREATE TABLE IF NOT EXISTS foods_queries (
        q TEXT PRIMARY KEY,
        fetched_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    """CREATE TABLE IF NOT EXISTS foods_meta (
        key TEXT PRIMARY KEY,
        value TEXT
    )""",
]

# Пакет продуктов, заранее скачанный из Open Food Facts (лежит рядом с server.py)
PRODUCTS_FILES = ("products_ru.json.gz", "products_ru.json")
OFF_BACKOFF_SEC = 600    # если Open Food Facts ответил ошибкой — не дёргаем его 10 минут
_off_down_until = 0.0

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
                   serving_g, serving_name, barcode, image_url, status, updated_at)
VALUES (%(source)s, %(source_id)s, %(name)s, %(brand)s, %(kcal)s, %(protein)s, %(fat)s,
        %(carbs)s, %(serving_g)s, %(serving_name)s, %(barcode)s, %(image_url)s, 'approved', now())
ON CONFLICT (source, source_id) DO UPDATE SET
    name = EXCLUDED.name, brand = EXCLUDED.brand, kcal = EXCLUDED.kcal,
    protein = EXCLUDED.protein, fat = EXCLUDED.fat, carbs = EXCLUDED.carbs,
    serving_g = EXCLUDED.serving_g, serving_name = EXCLUDED.serving_name,
    barcode = EXCLUDED.barcode, image_url = COALESCE(EXCLUDED.image_url, foods.image_url),
    status = 'approved', updated_at = now()
"""
_EMPTY = {"brand": None, "protein": None, "fat": None, "carbs": None, "serving_g": None,
          "serving_name": None, "barcode": None, "image_url": None}


def _upsert(cur, foods):
    if foods:
        cur.executemany(UPSERT, [{**_EMPTY, **f} for f in foods])


# ---------------------------------------------------------------- сети

def _slug(s):
    return re.sub(r"[^a-zа-я0-9]+", "-", str(s).lower()).strip("-")


def _r1(v):
    return None if v is None else round(float(v), 1)


def _menu_files():
    """Файлы меню: из папки restaurants/ и (если загружены без папки) рядом с server.py."""
    paths, seen = [], set()
    for path in sorted(glob.glob(os.path.join(RESTAURANTS_DIR, "*.json"))) + \
            sorted(glob.glob(os.path.join(HERE, "*.json"))):
        name = os.path.basename(path)
        if name.startswith("_") or name in seen:
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            continue
        if isinstance(data, dict) and data.get("chain") and isinstance(data.get("items"), list):
            seen.add(name)
            paths.append((path, data))
    if not paths:
        print("Меню сетей: файлы не найдены (ожидаются rostics.json и др.)", flush=True)
    return paths


def load_restaurants(pool):
    total = 0
    for path, data in _menu_files():
        try:
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
    pfc = [_num(n.get(k)) for k in ("proteins_100g", "fat_100g", "carbohydrates_100g")]
    if kcal is None and None not in pfc:  # калорий нет, но есть БЖУ — считаем 4/9/4
        kcal = round(pfc[0] * 4 + pfc[1] * 9 + pfc[2] * 4, 1)
    if any(v is not None and not (0 <= v <= 100) for v in pfc):
        return None
    name = html.unescape(str(p.get("product_name_ru") or p.get("product_name") or ""))
    name = re.sub(r"\s+", " ", name).strip()
    code = str(p.get("code") or "").strip()
    if not name or not code or kcal is None or kcal > 950:
        return None
    if not re.search(r"[а-яА-ЯёЁ]", name):  # в приложении на русском — берём русские названия
        return None
    if len(name) < 3 or JUNK_NAME.search(name):  # служебные пометки вроде «смотка на витрине»
        return None
    if None not in pfc and kcal > 40:  # калории сильно расходятся с БЖУ — данные с ошибкой
        calc = pfc[0] * 4 + pfc[1] * 9 + pfc[2] * 4
        if abs(calc - kcal) / kcal > 0.35:
            return None
    brands = p.get("brands") or ""
    if isinstance(brands, list):
        brands = brands[0] if brands else ""
    brand = html.unescape(str(brands).split(",")[0]).strip() or None
    name = name[:1].upper() + name[1:]
    return {"source": "off", "source_id": code, "barcode": code, "name": name[:100],
            "brand": brand[:80] if brand else None, "kcal": kcal,
            "protein": pfc[0], "fat": pfc[1], "carbs": pfc[2],
            "serving_g": _num(p.get("serving_quantity")), "image_url": off_image(code, p)}


JUNK_NAME = re.compile(r"смотк|витрин|паллет|снять|холодильник|\bполк|\d+\s*р\b|на дет\.? питан", re.I)


def off_image(code, p):
    """Ссылка на фото упаковки (200 px). Фото Open Food Facts — лицензия CC BY-SA."""
    url = p.get("image_front_small_url")
    if isinstance(url, str) and url.startswith("https://images.openfoodfacts.org/"):
        return url[:300]
    images = p.get("images")
    if not isinstance(images, dict) or not code.isdigit():
        return None
    key = next((k for k in ("front_ru", "front_en", "front") if isinstance(images.get(k), dict)), None) \
        or next((k for k in images if k.startswith("front_") and isinstance(images[k], dict)), None)
    if not key or not images[key].get("rev"):
        return None
    c = code.zfill(13) if len(code) > 8 else code
    folder = f"{c[0:3]}/{c[3:6]}/{c[6:9]}/{c[9:]}" if len(c) > 8 else c
    return f"https://images.openfoodfacts.org/images/products/{folder}/{key}.{images[key]['rev']}.200.jpg"


def _off_allowed():
    with _off_lock:
        now = time.time()
        while _off_calls and now - _off_calls[0] > 60:
            _off_calls.pop(0)
        if len(_off_calls) >= OFF_PER_MINUTE:
            return False
        _off_calls.append(now)
        return True


OFF_HOSTS = ("https://world.openfoodfacts.org", "https://ru.openfoodfacts.org")


def _off_get(params):
    """Запрос к Open Food Facts: два адреса, повтор при перегрузке, понятная ошибка в логе."""
    last = "нет ответа"
    for attempt, host in enumerate(OFF_HOSTS):
        req = urllib.request.Request(
            host + "/cgi/search.pl?" + urllib.parse.urlencode(params),
            headers={"User-Agent": OFF_UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return json.load(r)
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read(200).decode("utf-8", "replace").replace("\n", " ")
            except Exception:
                pass
            last = f"HTTP {exc.code} {exc.reason} от {host}: {body[:150]}"
            if exc.code in (429, 502, 503, 504) and attempt == 0:
                time.sleep(1.0)
        except Exception as exc:
            last = f"{type(exc).__name__} от {host}: {exc}"
    raise RuntimeError(last)


SAL_URL = "https://search.openfoodfacts.org/search"


def _sal_search(q):
    """Новый поиск Open Food Facts (search-a-licious): быстрый, понимает русский."""
    url = SAL_URL + "?" + urllib.parse.urlencode(
        {"q": q, "langs": "ru", "page_size": 50, "fields": OFF_FIELDS})
    req = urllib.request.Request(url, headers={"User-Agent": OFF_UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=6) as r:
            return json.load(r).get("hits") or []
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} {exc.reason} от search.openfoodfacts.org")
    except Exception as exc:
        raise RuntimeError(f"{type(exc).__name__} от search.openfoodfacts.org: {exc}")


def off_search(q):
    errors = []
    try:
        hits = _sal_search(q)
        out, seen = [], set()
        for p in hits:
            f = _parse_off(p)
            if f and f["source_id"] not in seen:
                seen.add(f["source_id"])
                out.append(f)
        return out
    except Exception as exc:
        errors.append(str(exc))
    try:
        return _cgi_search(q)
    except Exception as exc:
        errors.append(str(exc))
    raise RuntimeError("; ".join(errors))


def _cgi_search(q):
    params = {"search_terms": q, "search_simple": 1, "action": "process", "json": 1,
              "page_size": 40, "fields": OFF_FIELDS, "lc": "ru",
              "tagtype_0": "countries", "tag_contains_0": "contains", "tag_0": "russia"}
    data = _off_get(params)
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
    global _off_down_until
    if row or time.time() < _off_down_until or not _off_allowed():
        return False
    try:
        foods = off_search(q)
    except Exception as exc:
        _off_down_until = time.time() + OFF_BACKOFF_SEC
        print("Open Food Facts недоступен (пауза 10 мин):", exc, flush=True)
        return False
    with pool.connection() as conn:
        with conn.cursor() as cur:
            _upsert(cur, foods)
            cur.execute("INSERT INTO foods_queries (q) VALUES (%s) "
                        "ON CONFLICT (q) DO UPDATE SET fetched_at = now()", (q,))
    return bool(foods)


# ---------------------------------------------------------------- пакет продуктов

BULK_SQL = """
INSERT INTO foods (source, source_id, name, brand, kcal, protein, fat, carbs, serving_g,
                   barcode, status, updated_at)
SELECT 'off', t.c, t.n, NULLIF(t.b, ''), t.k, t.p, t.f, t.cb, t.s, t.c, 'approved', now()
FROM unnest(%s::text[], %s::text[], %s::text[], %s::float8[], %s::float8[], %s::float8[],
            %s::float8[], %s::float8[]) AS t(c, n, b, k, p, f, cb, s)
ON CONFLICT (source, source_id) DO UPDATE SET
    name = EXCLUDED.name, brand = EXCLUDED.brand, kcal = EXCLUDED.kcal,
    protein = EXCLUDED.protein, fat = EXCLUDED.fat, carbs = EXCLUDED.carbs,
    serving_g = EXCLUDED.serving_g, barcode = EXCLUDED.barcode, status = 'approved',
    updated_at = now()
"""


def load_products_pack(pool):
    """Заливает products_ru.json(.gz) в таблицу foods. Повторно — только если файл изменился."""
    import gzip
    import hashlib
    path = next((os.path.join(HERE, n) for n in PRODUCTS_FILES
                 if os.path.exists(os.path.join(HERE, n))), None)
    if not path:
        return 0
    with open(path, "rb") as fh:
        raw = fh.read()
    digest = hashlib.sha256(raw).hexdigest()
    with pool.connection() as conn:
        row = conn.execute("SELECT value FROM foods_meta WHERE key = 'products_pack'").fetchone()
    if row and row[0] == digest:
        print("Пакет продуктов: без изменений, загрузка не нужна", flush=True)
        return 0
    data = json.loads(gzip.decompress(raw) if path.endswith(".gz") else raw)
    items = [it for it in data["items"] if it and it[0] and it[1] and it[3] is not None]
    for i in range(0, len(items), 2000):
        chunk = items[i:i + 2000]
        cols = list(zip(*chunk))
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(BULK_SQL, [list(cols[0]), list(cols[1]), [b or "" for b in cols[2]],
                                       list(cols[3]), list(cols[4]), list(cols[5]),
                                       list(cols[6]), list(cols[7])])
    with pool.connection() as conn:
        conn.execute("INSERT INTO foods_meta (key, value) VALUES ('products_pack', %s) "
                     "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (digest,))
    print(f"Пакет продуктов: загружено {len(items)}", flush=True)
    return len(items)


# ---------------------------------------------------------------- поиск

SEARCH_SQL = """
SELECT name, brand, kcal, protein, fat, carbs, serving_g, serving_name, source, image_url
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
    items = [{"name": r[0], "brand": r[1], "cal": r[2], "p": r[3] or 0, "f": r[4] or 0,
              "c": r[5] or 0, "g": round(r[6]) if r[6] else 100, "portion": r[7], "src": r[8],
              "img": r[9]}
             for r in rows]
    return tidy(items)


PORTION_TAIL = re.compile(
    r"\s+(\d+\s*шт\.?|\d+[.,]?\d*\s*л|маленьк\w*|средн\w*|больш\w*|мал(ый|ые|ая)|баскет)$", re.I)


def _base_name(name):
    m = PORTION_TAIL.search(name)
    return (name[:m.start()].strip(), m.group(1)) if m else (name, None)


def _dkey(s):
    return re.sub(r"[\W_]+", "", _norm(s))


def tidy(items):
    """Склеивает порции одного блюда сети в одну позицию и убирает повторы товаров."""
    out, groups, seen = [], {}, set()
    for it in items:
        if it["src"] == "restaurant":
            base, tail = _base_name(it["name"])
            key = ("r", it["brand"], _norm(base))
            label = it.get("portion") if it.get("portion") and it.get("portion") != "1 порция" else tail
            portion = {"label": (label or "порция").strip(), "g": it["g"]}
            g = groups.get(key)
            if g and abs(g["cal"] - it["cal"]) <= max(8, g["cal"] * 0.1):
                if all(x["g"] != portion["g"] for x in g["portions"]):
                    g["portions"].append(portion)
                continue
            it = dict(it, name=base if tail else it["name"], portions=[portion])
            groups[key] = it
            out.append(it)
            continue
        key = (_dkey(it["name"]), _dkey(it.get("brand")))
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    for it in out:
        if it.get("portions"):
            it["portions"].sort(key=lambda x: x["g"])
            it["g"] = it["portions"][0]["g"]
            if len(it["portions"]) == 1 and it["portions"][0]["label"] == "порция":
                it["portions"] = []
    return out


def search(pool, q="", brand=None, limit=40):
    text, alias_brand = parse_query(q)
    brand = (brand or alias_brand or None)
    if brand:
        brand = str(brand)[:80]
    words = [x for x in re.split(r"\s+", text.replace("%", "").replace("_", "")) if x][:4]
    if not words and not brand:
        return []
    if not words:  # просмотр меню сети целиком
        return _local(pool, [], brand, 400)
    items = _local(pool, words, brand, limit * 3)
    if len(items) < MIN_LOCAL and not brand and len(text) >= 3:
        if _fetch_off_if_needed(pool, text):
            items = _local(pool, words, brand, limit * 3)
    return items[:limit]


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
        # убираем из поиска служебный мусор, сохранённый до появления фильтра
        conn.execute("UPDATE foods SET status = 'archived' WHERE source = 'off' AND status = 'approved' "
                     "AND name ~* 'смотк|витрин|паллет|снять|холодильник|на дет\\.? питан'")
    if _started:
        return
    _started = True

    def loop():
        try:
            load_products_pack(pool)
        except Exception as exc:
            print("Пакет продуктов не загрузился:", repr(exc), flush=True)
        while True:
            try:
                load_restaurants(pool)
            except Exception as exc:
                print("Обновление меню сетей не удалось:", repr(exc), flush=True)
            time.sleep(REFRESH_HOURS * 3600)

    threading.Thread(target=loop, daemon=True, name="foods-refresh").start()
