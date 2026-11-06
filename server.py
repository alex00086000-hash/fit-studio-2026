from datetime import datetime
import http.server
import json
import os
import random
import sqlite3
import urllib.parse

PORT = int(os.environ.get("PORT", 8000))
DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "cloud_tracker_v2.db"
)

# Каталог продуктов (на 100 г: ккал, белки, жиры, углеводы)
DEFAULT_FOODS = [
    ("Куриное филе грудки (вареное/печеное)", 165, 31.0, 3.6, 0.0),
    ("Яйцо вареное (2 шт ~ 100г)", 155, 13.0, 11.0, 1.1),
    ("Творог 5%", 121, 17.0, 5.0, 3.0),
    ("Гречневая каша на воде", 110, 4.2, 1.1, 21.3),
    ("Рис белый вареный", 130, 2.7, 0.3, 28.0),
    ("Овсяная каша на воде", 88, 3.0, 1.7, 15.0),
    ("Макароны твердых сортов (вареные)", 140, 5.5, 0.7, 28.0),
    ("Говядина постная (отварная/запеченная)", 187, 26.0, 9.0, 0.0),
    ("Индейка филе (готовое)", 150, 29.0, 3.0, 0.0),
    ("Сыр твердый 45%", 350, 25.0, 28.0, 0.5),
    ("Хлеб цельнозерновой", 220, 7.0, 1.5, 43.0),
    ("Картофель отварной / запеченный", 82, 2.0, 0.4, 16.7),
    ("Огурцы свежие", 15, 0.8, 0.1, 2.8),
    ("Помидоры свежие", 20, 0.9, 0.2, 3.9),
    ("Яблоко свежее", 52, 0.3, 0.2, 13.8),
    ("Банан", 95, 1.5, 0.2, 21.8),
    ("Сметана 15%", 160, 2.6, 15.0, 3.6),
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

# Шаблоны сбалансированных приемов пищи для генератора рациона
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
            ("Говядина постная (отварная/запеченная)", 0.50),
            ("Макароны твердых сортов (вареные)", 0.40),
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
            ("Яйцо вареное (2 шт ~ 100г)", 0.50),
            ("Творог 5%", 0.40),
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


def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(
            """CREATE TABLE IF NOT EXISTS settings (
                telegram_id TEXT,
                key TEXT,
                value TEXT,
                PRIMARY KEY (telegram_id, key)
            )"""
        )
        cur.execute(
            """CREATE TABLE IF NOT EXISTS entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id TEXT,
                date TEXT,
                weight REAL,
                calories INTEGER,
                steps INTEGER,
                note TEXT,
                water_ml INTEGER DEFAULT 0,
                UNIQUE(telegram_id, date)
            )"""
        )
        cur.execute(
            """CREATE TABLE IF NOT EXISTS meal_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id TEXT,
                date TEXT,
                meal_type TEXT DEFAULT 'breakfast',
                food_name TEXT,
                grams REAL,
                calories REAL,
                protein REAL DEFAULT 0,
                fat REAL DEFAULT 0,
                carbs REAL DEFAULT 0
            )"""
        )
        cur.execute(
            """CREATE TABLE IF NOT EXISTS workout_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id TEXT,
                date TEXT,
                workout_name TEXT,
                duration_min REAL,
                calories_burned REAL
            )"""
        )

        # Безопасное добавление новых колонок к существующей базе
        cur.execute("PRAGMA table_info(entries)")
        e_cols = [c[1] for c in cur.fetchall()]
        if "water_ml" not in e_cols:
            cur.execute(
                "ALTER TABLE entries ADD COLUMN water_ml INTEGER DEFAULT 0"
            )

        cur.execute("PRAGMA table_info(meal_log)")
        m_cols = [c[1] for c in cur.fetchall()]
        if "meal_type" not in m_cols:
            cur.execute(
                "ALTER TABLE meal_log ADD COLUMN meal_type TEXT DEFAULT 'breakfast'"
            )

        conn.commit()


def extract_user_id(init_data_str, fallback_id="default_user"):
    if not init_data_str:
        return fallback_id
    try:
        parsed = dict(
            urllib.parse.parse_qsl(init_data_str, keep_blank_values=True)
        )
        user_obj = json.loads(parsed.get("user", "{}"))
        uid = user_obj.get("id")
        return str(uid) if uid else fallback_id
    except Exception:
        return fallback_id


def get_user_settings(cur, user_id):
    cur.execute(
        "SELECT key, value FROM settings WHERE telegram_id = ?", (user_id,)
    )
    rows = dict(cur.fetchall())
    return {
        "target_weight": float(rows.get("target_weight", 73.0)),
        "target_cal": int(float(rows.get("target_cal", 1850))),
        "target_p": int(float(rows.get("target_p", 138))),
        "target_f": int(float(rows.get("target_f", 62))),
        "target_c": int(float(rows.get("target_c", 185))),
        "target_water": int(float(rows.get("target_water", 2250))),
        "age": int(float(rows.get("age", 29))),
        "height": float(rows.get("height", 167.0)),
        "start_weight": float(rows.get("start_weight", 93.0)),
    }


def save_setting(cur, user_id, key, val):
    cur.execute(
        """INSERT INTO settings (telegram_id, key, value) VALUES (?, ?, ?)
           ON CONFLICT(telegram_id, key) DO UPDATE SET value = excluded.value""",
        (user_id, key, str(val)),
    )


def sync_daily_calories(cur, user_id, date_str):
    cur.execute(
        "SELECT SUM(calories) FROM meal_log WHERE telegram_id = ? AND date = ?",
        (user_id, date_str),
    )
    total_cal = int(round(cur.fetchone()[0] or 0))
    cur.execute(
        """INSERT INTO entries (telegram_id, date, weight, calories, steps, note, water_ml)
           VALUES (?, ?, 0, ?, 0, '', 0)
           ON CONFLICT(telegram_id, date) DO UPDATE SET calories = excluded.calories""",
        (user_id, date_str, total_cal),
    )


WEB_APP_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<title>Fit Studio 2026 — Fitia Edition</title>
<style>
  :root {
    --lime: #C6F16D;
    --bg: #E7F9B8;
    --card: #14230D;
    --card2: #1C3113;
    --input: #243E18;
    --accent: #9AE61C;
    --text: #F8FAFC;
    --muted: #93B082;
    --warn: #F43F5E;
    --blue: #38BDF8;
    --amber: #F59E0B;
    --green: #22C55E;
  }
  * { box-sizing: border-box; font-family: 'Segoe UI', system-ui, -apple-system, sans-serif; -webkit-tap-highlight-color: transparent; }
  body { margin: 0; padding: 12px 12px 86px; background: var(--lime); color: var(--card); }
  .container { max-width: 580px; margin: 0 auto; }

  /* Шапка с датой */
  .top-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; gap: 8px; }
  .brand-title { font-size: 18px; font-weight: 900; letter-spacing: -0.3px; display: flex; align-items: center; gap: 6px; }
  .date-pill { display: flex; align-items: center; background: var(--card); color: var(--text); padding: 4px 10px; border-radius: 20px; gap: 6px; }
  .date-pill input { background: transparent; border: none; color: var(--accent); font-weight: 800; font-size: 13px; width: 112px; outline: none; }

  /* Главная панель КБЖУ в стиле Fitia */
  .fitia-hero { background: var(--card); color: var(--text); border-radius: 20px; padding: 16px; margin-bottom: 12px; box-shadow: 0 8px 20px rgba(20,35,13,0.18); }
  .cal-summary { display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; text-align: center; gap: 8px; margin-bottom: 14px; }
  .cal-stat .num { font-size: 18px; font-weight: 800; }
  .cal-stat .lbl { font-size: 11px; color: var(--muted); }

  .ring-box { position: relative; width: 116px; height: 116px; display: flex; flex-direction: column; align-items: center; justify-content: center; border-radius: 50%; border: 7px solid var(--input); }
  .ring-box.active { border-color: var(--accent); }
  .ring-box.over { border-color: var(--warn); }
  .ring-val { font-size: 24px; font-weight: 900; line-height: 1; color: var(--accent); }
  .ring-lbl { font-size: 10px; color: var(--muted); margin-top: 3px; text-transform: uppercase; font-weight: 700; }

  .macros-row { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
  .macro-card { background: var(--card2); padding: 9px 10px; border-radius: 12px; }
  .macro-head { display: flex; justify-content: space-between; font-size: 11px; font-weight: 700; margin-bottom: 5px; }
  .p-bar-bg { width: 100%; height: 7px; background: var(--input); border-radius: 6px; overflow: hidden; }
  .p-bar-fill { height: 100%; width: 0%; border-radius: 6px; transition: width 0.3s ease; }
  .macro-sub { font-size: 11px; color: var(--muted); margin-top: 4px; text-align: right; font-weight: 600; }

  /* Кнопка Fitia AI Авто-меню */
  .ai-banner { background: linear-gradient(135deg, #1E3514 0%, #14230D 100%); color: var(--text); border: 2px solid var(--accent); border-radius: 16px; padding: 12px 14px; margin-bottom: 12px; display: flex; justify-content: space-between; align-items: center; gap: 10px; }
  .ai-banner h4 { margin: 0 0 2px; font-size: 14px; color: var(--accent); }
  .ai-banner p { margin: 0; font-size: 11px; color: var(--muted); }
  .ai-btn { background: var(--accent); color: var(--card); border: none; padding: 9px 13px; border-radius: 12px; font-weight: 900; font-size: 12px; cursor: pointer; white-space: nowrap; }

  /* Карточка воды */
  .water-card { background: var(--card); color: var(--text); border-radius: 16px; padding: 13px 15px; margin-bottom: 12px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; }
  .water-info h4 { margin: 0; font-size: 13px; color: var(--blue); }
  .water-info .w-val { font-size: 16px; font-weight: 800; margin-top: 2px; }
  .water-glasses { display: flex; gap: 4px; font-size: 16px; margin-top: 4px; }
  .water-btns { display: flex; gap: 6px; }
  .water-btns button { background: var(--input); color: var(--blue); border: 1px solid var(--blue); padding: 7px 11px; border-radius: 10px; font-weight: 800; font-size: 12px; cursor: pointer; }

  /* Карточки приемов пищи (Завтрак / Обед / Ужин / Перекус) */
  .meal-card { background: var(--card); color: var(--text); border-radius: 16px; padding: 14px; margin-bottom: 10px; }
  .meal-header { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--input); padding-bottom: 8px; margin-bottom: 8px; }
  .meal-title { font-size: 15px; font-weight: 800; display: flex; align-items: center; gap: 6px; }
  .meal-kcal { font-size: 13px; color: var(--accent); font-weight: 800; }
  .meal-add-btn { background: var(--accent); color: var(--card); border: none; border-radius: 10px; padding: 5px 11px; font-weight: 900; font-size: 12px; cursor: pointer; }

  .food-item { display: flex; justify-content: space-between; align-items: center; padding: 6px 0; border-bottom: 1px dashed rgba(147,176,130,0.18); font-size: 13px; }
  .food-item:last-child { border-bottom: none; }
  .food-name { font-weight: 600; }
  .food-meta { font-size: 11px; color: var(--muted); }
  .food-right { display: flex; align-items: center; gap: 8px; }
  .food-cal { font-weight: 800; color: var(--text); font-size: 13px; }
  .del-btn { background: rgba(244,63,94,0.2); color: var(--warn); border: none; border-radius: 6px; width: 24px; height: 24px; font-weight: 800; cursor: pointer; }

  /* Общие элементы форм и вкладок */
  .tab-view { display: none; }
  .tab-view.active { display: block; }
  .box-card { background: var(--card); color: var(--text); border-radius: 16px; padding: 16px; margin-bottom: 12px; }
  .box-card h3 { margin: 0 0 8px; color: var(--accent); font-size: 14px; }
  input, select { width: 100%; padding: 10px; margin: 4px 0; border-radius: 10px; border: none; background: var(--input); color: var(--text); font-size: 13px; }
  .btn-main { width: 100%; padding: 11px; margin-top: 8px; border-radius: 10px; border: none; background: var(--accent); color: var(--card); font-weight: 900; font-size: 14px; cursor: pointer; }
  .btn-danger { background: var(--warn); color: var(--text); }
  .row-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  .row-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; }

  /* Модальное окно добавления еды */
  .modal-backdrop { position: fixed; inset: 0; background: rgba(0,0,0,0.65); display: none; align-items: center; justify-content: center; padding: 14px; z-index: 200; }
  .modal-backdrop.open { display: flex; }
  .modal-card { background: var(--card); color: var(--text); width: 100%; max-width: 420px; border-radius: 18px; padding: 18px; border: 2px solid var(--accent); }
  .modal-top { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
  .modal-top h3 { margin: 0; color: var(--accent); font-size: 16px; }
  .close-btn { background: var(--input); color: var(--text); border: none; border-radius: 8px; padding: 4px 10px; cursor: pointer; font-weight: 800; }

  /* Нижнее меню в стиле Fitia */
  .bottom-nav { position: fixed; bottom: 0; left: 0; right: 0; background: var(--card); border-top: 2px solid var(--input); display: flex; justify-content: space-around; padding: 8px 6px 12px; z-index: 100; }
  .nav-btn { background: transparent; border: none; color: var(--muted); font-size: 11px; font-weight: 700; display: flex; flex-direction: column; align-items: center; gap: 3px; cursor: pointer; width: 25%; }
  .nav-btn .ico { font-size: 18px; }
  .nav-btn.active { color: var(--accent); }

  canvas { width: 100%; height: 170px; background: var(--input); border-radius: 10px; margin-top: 8px; }
  table { width: 100%; border-collapse: collapse; margin-top: 8px; font-size: 12px; }
  th, td { text-align: left; padding: 7px 5px; border-bottom: 1px solid var(--input); }
  th { color: var(--accent); }
</style>
</head>
<body>

<div class="container">
  <!-- Верхняя панель с выбором даты -->
  <div class="top-header">
    <div class="brand-title">🧩 FITIA STUDIO <span id="userBadge" style="font-size:11px; font-weight:700; opacity:0.8;"></span></div>
    <div class="date-pill">
      <span>📅</span>
      <input type="date" id="currentDate" onchange="refresh()">
    </div>
  </div>

  <!-- ВКЛАДКА 1: ДНЕВНИК FITIA -->
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

    <!-- Баннер автоподбора рациона -->
    <div class="ai-banner">
      <div>
        <h4>✨ Умный план питания Fitia</h4>
        <p>Автоподбор граммовок на день под вашу норму КБЖУ</p>
      </div>
      <button class="ai-btn" onclick="generateSmartDay()">Собрать день</button>
    </div>

    <!-- Трекер воды -->
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

    <!-- 4 Приема пищи -->
    <div id="mealsContainer"></div>
  </div>

  <!-- ВКЛАДКА 2: УМНЫЙ ГЕНЕРАТОР РАЦИОНА И СПИСОК ПРОДУКТОВ -->
  <div id="tab-plan" class="tab-view">
    <div class="box-card">
      <h3>✨ Автогенератор меню на день (Fitia AI)</h3>
      <p style="font-size:12px; color:var(--muted); margin-top:0;">
        Алгоритм распределяет вашу дневную норму калорий на <b>Завтрак (25%)</b>, <b>Обед (35%)</b>, <b>Ужин (25%)</b> и <b>Перекус (15%)</b> из простых продуктов (курица, индейка, говядина, вареные яйца, творог, крупы, свежие овощи и фрукты).
      </p>
      <button class="btn-main" onclick="generateSmartDay()">🔄 Сгенерировать новое меню на выбранный день</button>
      <button class="btn-main btn-danger" style="margin-top:6px;" onclick="clearDayMeals()">🗑️ Очистить рацион за этот день</button>
    </div>

    <div class="box-card">
      <h3>🛒 Список продуктов на сегодня (по дневнику)</h3>
      <div id="shoppingList" style="font-size:13px; color:var(--text);"></div>
    </div>
  </div>

  <!-- ВКЛАДКА 3: ВЕС, ШАГИ, ТРЕНИРОВКИ И ГРАФИК -->
  <div id="tab-progress" class="tab-view">
    <div class="box-card">
      <h3>⚖️ Утренний вес и шаги за день</h3>
      <div class=" id-sub" id="weightSummary" style="font-size:13px; color:var(--muted); margin-bottom:8px;"></div>
      <div class="row-2">
        <input type="number" step="0.1" id="wInput" placeholder="Вес утром (кг)">
        <input type="number" id="sInput" placeholder="Пройдено шагов">
      </div>
      <input type="text" id="noteInput" placeholder="Заметка / самочувствие">
      <button class="btn-main" onclick="sendEntry()">Сохранить вес и шаги</button>
    </div>

    <div class="box-card">
      <h3>🏋️ Добавить тренировку / прогулку</h3>
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
      <h3>📈 График снижения веса</h3>
      <canvas id="weightChart" width="520" height="180"></canvas>
    </div>

    <div class="box-card">
      <h3>📋 История записей по дням</h3>
      <table id="historyTable">
        <thead><tr><th>Дата</th><th>Вес</th><th>Ккал</th><th>Шаги</th><th>Вода</th><th></th></tr></thead>
        <tbody></tbody>
      </table>
    </div>
  </div>

  <!-- ВКЛАДКА 4: ПРОФИЛЬ И КАЛЬКУЛЯТОР НОРМ FITIA -->
  <div id="tab-profile" class="tab-view">
    <div class="box-card">
      <h3>🎯 Мои параметры и авторасчет норм БЖУ</h3>
      <p style="font-size:12px; color:var(--muted); margin-top:0;">
        Рассчитайте индивидуальный дефицит по формуле Миффлина — Сан Жеора, и приложение автоматически обновит шкалы калорий, белков, жиров и углеводов.
      </p>
      <div class="row-2">
        <div>
          <label style="font-size:11px; color:var(--muted)">Возраст (лет)</label>
          <input type="number" id="cAge" value="29">
        </div>
        <div>
          <label style="font-size:11px; color:var(--muted)">Рост (см)</label>
          <input type="number" id="cHeight" value="167">
        </div>
      </div>
      <div class="row-2">
        <div>
          <label style="font-size:11px; color:var(--muted)">Текущий вес (кг)</label>
          <input type="number" step="0.1" id="cWeight" value="93">
        </div>
        <div>
          <label style="font-size:11px; color:var(--muted)">Целевой вес (кг)</label>
          <input type="number" step="0.1" id="cTargetWeight" value="73">
        </div>
      </div>
      <label style="font-size:11px; color:var(--muted)">Уровень дневной активности</label>
      <select id="cAct">
        <option value="1.2">Сидячая работа за ПК (1.2)</option>
        <option value="1.375" selected>Прогулки / домашние тренировки (1.375)</option>
        <option value="1.55">Интенсивные тренировки 3-5 раз в нед. (1.55)</option>
      </select>
      <button class="btn-main" onclick="calculateAndSaveGoals()">Рассчитать и применить нормы для Fitia</button>
    </div>

    <div class="box-card">
      <h3>⚙️ Ручная настройка дневных целей</h3>
      <div class="row-2">
        <div>
          <label style="font-size:11px; color:var(--muted)">Норма калорий (ккал)</label>
          <input type="number" id="gCal">
        </div>
        <div>
          <label style="font-size:11px; color:var(--muted)">Норма воды (мл)</label>
          <input type="number" id="gWater">
        </div>
      </div>
      <div class="row-3">
        <div>
          <label style="font-size:11px; color:var(--blue)">Белки (г)</label>
          <input type="number" id="gP">
        </div>
        <div>
          <label style="font-size:11px; color:var(--amber)">Жиры (г)</label>
          <input type="number" id="gF">
        </div>
        <div>
          <label style="font-size:11px; color:var(--green)">Углеводы (г)</label>
          <input type="number" id="gC">
        </div>
      </div>
      <button class="btn-main" onclick="saveManualGoals()">Сохранить цели</button>
    </div>
  </div>
</div>

<!-- Модальное окно добавления продукта в выбранный прием пищи -->
<div class="modal-backdrop" id="mealModal">
  <div class="modal-card">
    <div class="modal-top">
      <h3 id="modalTitle">Добавить в Завтрак</h3>
      <button class="close-btn" onclick="closeMealModal()">✕</button>
    </div>
    <label style="font-size:11px; color:var(--muted)">Выберите из каталога:</label>
    <select id="foodSelect" onchange="onFoodSelect()"></select>
    <input type="text" id="fName" placeholder="Название блюда / продукта">
    <div class="row-2">
      <div>
        <label style="font-size:11px; color:var(--muted)">Вес порции (г)</label>
        <input type="number" id="fGrams" value="150" oninput="onFoodSelect()">
      </div>
      <div>
        <label style="font-size:11px; color:var(--muted)">Калории (ккал)</label>
        <input type="number" id="fCal" placeholder="Ккал">
      </div>
    </div>
    <div class="row-3">
      <input type="number" step="0.1" id="fP" placeholder="Белки (г)">
      <input type="number" step="0.1" id="fF" placeholder="Жиры (г)">
      <input type="number" step="0.1" id="fC" placeholder="Углеводы (г)">
    </div>
    <button class="btn-main" onclick="submitMeal()">+ Записать в рацион</button>
  </div>
</div>

<!-- Нижняя навигация как в мобильном приложении Fitia -->
<div class="bottom-nav">
  <button class="nav-btn active" onclick="switchTab('diary', this)">
    <span class="ico">🥗</span><span>Дневник</span>
  </button>
  <button class="nav-btn" onclick="switchTab('plan', this)">
    <span class="ico">✨</span><span>Авто-меню</span>
  </button>
  <button class="nav-btn" onclick="switchTab('progress', this)">
    <span class="ico">📈</span><span>Вес & Шаги</span>
  </button>
  <button class="nav-btn" onclick="switchTab('profile', this)">
    <span class="ico">🎯</span><span>Нормы БЖУ</span>
  </button>
</div>

<script>
  const tg = (window.Telegram && window.Telegram.WebApp) ? window.Telegram.WebApp : null;
  if (tg) { tg.expand(); }

  const initData = (tg && tg.initData) ? tg.initData : "";
  let tgUserId = "default_user";
  if (tg && tg.initDataUnsafe && tg.initDataUnsafe.user) {
    const u = tg.initDataUnsafe.user;
    tgUserId = String(u.id || "default_user");
    document.getElementById('userBadge').innerText = "• " + (u.first_name || "TG");
  }

  const todayStr = new Date().toISOString().split('T')[0];
  document.getElementById('currentDate').value = todayStr;

  let foodCatalog = [];
  let workCatalog = [];
  let activeMealType = 'breakfast';

  const MEAL_META = [
    { key: 'breakfast', title: '🌅 Завтрак', pct: 25 },
    { key: 'lunch',     title: '☀️ Обед',    pct: 35 },
    { key: 'dinner',    title: '🌙 Ужин',    pct: 25 },
    { key: 'snack',     title: '🍏 Перекус', pct: 15 }
  ];

  function switchTab(tabName, btn) {
    document.querySelectorAll('.tab-view').forEach(el => el.classList.remove('active'));
    document.getElementById('tab-' + tabName).classList.add('active');
    document.querySelectorAll('.nav-btn').forEach(el => el.classList.remove('active'));
    if (btn) btn.classList.add('active');
  }

  async function apiFetch(url, options = {}) {
    options.headers = options.headers || {};
    options.headers['X-Telegram-Init-Data'] = initData;
    options.headers['X-Fallback-User'] = tgUserId;
    const res = await fetch(url, options);
    return res.json();
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

  async function refresh() {
    const date = document.getElementById('currentDate').value || todayStr;
    const d = await apiFetch('/api/state?date=' + encodeURIComponent(date));

    // Заполнение каталогов
    if (foodCatalog.length === 0 && d.foods) {
      foodCatalog = d.foods;
      const fSel = document.getElementById('foodSelect');
      fSel.innerHTML = '<option value="">-- Выберите продукт из базы --</option>';
      foodCatalog.forEach((f, i) => {
        fSel.innerHTML += `<option value="${i}">${f.name} (${f.cal} ккал | Б:${f.p} Ж:${f.f} У:${f.c})</option>`;
      });
    }
    if (workCatalog.length === 0 && d.workouts) {
      workCatalog = d.workouts;
      const wSel = document.getElementById('workSelect');
      wSel.innerHTML = '<option value="">-- Выберите тренировку --</option>';
      workCatalog.forEach((w, i) => {
        wSel.innerHTML += `<option value="${i}">${w.name} (${w.cal_min} ккал/мин)</option>`;
      });
    }

    const s = d.settings;
    document.getElementById('gCal').value = s.target_cal;
    document.getElementById('gP').value = s.target_p;
    document.getElementById('gF').value = s.target_f;
    document.getElementById('gC').value = s.target_c;
    document.getElementById('gWater').value = s.target_water;
    document.getElementById('cTargetWeight').value = s.target_weight;

    // Расчет верхнего дашборда калорий и БЖУ
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

    updateMacroBar('p', d.today_p, s.target_p);
    updateMacroBar('f', d.today_f, s.target_f);
    updateMacroBar('c', d.today_c, s.target_c);

    // Водный баланс
    const wMl = d.water_ml || 0;
    document.getElementById('waterText').innerText = `${wMl} / ${s.target_water} мл`;
    const totalGlasses = Math.max(Math.round(s.target_water / 250), 6);
    const filledGlasses = Math.floor(wMl / 250);
    let gHtml = '';
    for (let i = 0; i < totalGlasses; i++) {
      gHtml += i < filledGlasses ? '🥛' : '⚪';
    }
    document.getElementById('waterGlasses').innerHTML = gHtml;

    // Рендер 4 карточек приемов пищи
    const mCont = document.getElementById('mealsContainer');
    mCont.innerHTML = '';
    const mealsByType = { breakfast: [], lunch: [], dinner: [], snack: [] };
    (d.day_meals || []).forEach(m => {
      const k = mealsByType[m.meal_type] ? m.meal_type : 'breakfast';
      mealsByType[k].push(m);
    });

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
                <div class="food-name">${item.food_name}</div>
                <div class="food-meta">${Math.round(item.grams)} г • Б:${item.p.toFixed(1)} Ж:${item.f.toFixed(1)} У:${item.c.toFixed(1)}</div>
              </div>
              <div class="food-right">
                <span class="food-cal">${Math.round(item.calories)} ккал</span>
                <button class="del-btn" onclick="deleteMeal(${item.id})">✕</button>
              </div>
            </div>`;
        });
      }

      mCont.innerHTML += `
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

    // Список покупок во вкладке Авто-меню
    const shopDiv = document.getElementById('shoppingList');
    if ((d.day_meals || []).length === 0) {
      shopDiv.innerHTML = '<span style="color:var(--muted)">Нажмите «Собрать день», чтобы получить точный список продуктов с граммовками.</span>';
    } else {
      const totals = {};
      d.day_meals.forEach(m => {
        totals[m.food_name] = (totals[m.food_name] || 0) + m.grams;
      });
      shopDiv.innerHTML = Object.entries(totals).map(([name, gr]) =>
        `<div style="padding:6px 0; border-bottom:1px solid var(--input); display:flex; justify-content:space-between;">
          <span>• ${name}</span><b style="color:var(--accent)">${Math.round(gr)} г</b>
        </div>`
      ).join('');
    }

    // Вкладка Вес и Прогресс
    document.getElementById('weightSummary').innerText = `Текущий: ${d.curr_weight} | ${d.weight_sub}`;
    const wList = document.getElementById('workoutList');
    wList.innerHTML = (d.day_workouts || []).map(w =>
      `<div class="food-item">
        <span>🏃 ${w.name} (${Math.round(w.mins)} мин)</span>
        <div class="food-right">
          <b style="color:var(--warn)">🔥 ${Math.round(w.cal)} ккал</b>
          <button class="del-btn" onclick="deleteWorkout(${w.id})">✕</button>
        </div>
      </div>`
    ).join('');

    const tbody = document.querySelector('#historyTable tbody');
    tbody.innerHTML = '';
    (d.entries || []).slice().reverse().slice(0, 12).forEach(e => {
      tbody.innerHTML += `<tr>
        <td>${e.date}</td>
        <td><b>${e.weight > 0 ? e.weight + ' кг' : '-'}</b></td>
        <td>${e.calories}</td>
        <td>${e.steps}</td>
        <td>${e.water_ml || 0} мл</td>
        <td><button class="del-btn" onclick="deleteEntry('${e.date}')">✕</button></td>
      </tr>`;
    });

    drawChart(d.entries || [], s.target_weight);
  }

  function updateMacroBar(prefix, curr, target) {
    const pct = target > 0 ? Math.min(Math.round((curr / target) * 100), 100) : 0;
    document.getElementById(prefix + 'Pct').innerText = pct + '%';
    document.getElementById(prefix + 'Bar').style.width = pct + '%';
    document.getElementById(prefix + 'Text').innerText = `${Math.round(curr)} / ${target} г`;
  }

  async function submitMeal() {
    const date = document.getElementById('currentDate').value || todayStr;
    const name = document.getElementById('fName').value.trim();
    const grams = parseFloat(document.getElementById('fGrams').value || 100);
    const cal = parseFloat(document.getElementById('fCal').value || 0);
    const p = parseFloat(document.getElementById('fP').value || 0);
    const f = parseFloat(document.getElementById('fF').value || 0);
    const c = parseFloat(document.getElementById('fC').value || 0);
    if (!name) return;

    await apiFetch('/api/add_meal', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({date, meal_type: activeMealType, name, grams, cal, p, f, c})
    });
    closeMealModal();
    refresh();
  }

  async function deleteMeal(id) {
    const date = document.getElementById('currentDate').value || todayStr;
    await apiFetch('/api/delete_meal', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id, date})
    });
    refresh();
  }

  async function generateSmartDay() {
    const date = document.getElementById('currentDate').value || todayStr;
    await apiFetch('/api/generate_day', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({date})
    });
    refresh();
  }

  async function clearDayMeals() {
    const date = document.getElementById('currentDate').value || todayStr;
    await apiFetch('/api/clear_day_meals', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({date})
    });
    refresh();
  }

  async function addWater(delta) {
    const date = document.getElementById('currentDate').value || todayStr;
    await apiFetch('/api/add_water', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({date, delta})
    });
    refresh();
  }

  async function sendEntry() {
    const date = document.getElementById('currentDate').value || todayStr;
    const weight = parseFloat((document.getElementById('wInput').value || '0').replace(',', '.'));
    const steps = parseInt(document.getElementById('sInput').value || '0');
    const note = document.getElementById('noteInput').value.trim();
    await apiFetch('/api/add_entry', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({date, weight, steps, note})
    });
    document.getElementById('wInput').value = '';
    document.getElementById('sInput').value = '';
    document.getElementById('noteInput').value = '';
    refresh();
  }

  async function deleteEntry(date) {
    await apiFetch('/api/delete_entry', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({date})
    });
    refresh();
  }

  async function sendWork() {
    const date = document.getElementById('currentDate').value || todayStr;
    const name = document.getElementById('wkName').value.trim();
    const mins = parseFloat(document.getElementById('wkMin').value || 30);
    const cal = parseFloat(document.getElementById('wkCal').value || 100);
    if (!name) return;
    await apiFetch('/api/add_workout', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({date, name, mins, cal})
    });
    document.getElementById('wkName').value = '';
    document.getElementById('workSelect').value = '';
    refresh();
  }

  async function deleteWorkout(id) {
    await apiFetch('/api/delete_workout', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id})
    });
    refresh();
  }

  async function calculateAndSaveGoals() {
    const age = parseFloat(document.getElementById('cAge').value || 29);
    const h = parseFloat(document.getElementById('cHeight').value || 167);
    const w = parseFloat(document.getElementById('cWeight').value || 93);
    const tw = parseFloat(document.getElementById('cTargetWeight').value || 73);
    const act = parseFloat(document.getElementById('cAct').value || 1.375);

    const bmr = (10 * w) + (6.25 * h) - (5 * age) + 5;
    const tdee = bmr * act;
    const targetCal = Math.round(tdee * 0.80); // Дефицит 20%
    const targetP = Math.round((targetCal * 0.30) / 4);
    const targetF = Math.round((targetCal * 0.30) / 9);
    const targetC = Math.round((targetCal * 0.40) / 4);
    const targetWater = Math.round(w * 25);

    await apiFetch('/api/save_goals', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        target_weight: tw,
        target_cal: targetCal,
        target_p: targetP,
        target_f: targetF,
        target_c: targetC,
        target_water: targetWater
      })
    });
    await refresh();
    switchTab('diary', document.querySelectorAll('.nav-btn')[0]);
  }

  async function saveManualGoals() {
    await apiFetch('/api/save_goals', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        target_weight: parseFloat(document.getElementById('cTargetWeight').value || 73),
        target_cal: parseInt(document.getElementById('gCal').value || 1850),
        target_p: parseInt(document.getElementById('gP').value || 138),
        target_f: parseInt(document.getElementById('gF').value || 62),
        target_c: parseInt(document.getElementById('gC').value || 185),
        target_water: parseInt(document.getElementById('gWater').value || 2250)
      })
    });
    await refresh();
    switchTab('diary', document.querySelectorAll('.nav-btn')[0]);
  }

  function drawChart(entries, target) {
    const canvas = document.getElementById('weightChart');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const w = canvas.width, h = canvas.height;
    ctx.clearRect(0, 0, w, h);

    const valid = entries.filter(e => e.weight > 0);
    if (valid.length < 2) {
      ctx.fillStyle = '#93B082';
      ctx.font = '13px Segoe UI';
      ctx.textAlign = 'center';
      ctx.fillText('Добавьте минимум 2 записи веса в разные дни для графика', w / 2, h / 2);
      return;
    }

    const weights = valid.map(e => e.weight);
    let minW = Math.min(...weights);
    let maxW = Math.max(...weights);
    if (target > 0) { minW = Math.min(minW, target); maxW = Math.max(maxW, target); }
    minW -= 1.5; maxW += 1.5;
    const range = Math.max(maxW - minW, 1);
    const padX = 40, padY = 24;

    if (target > 0) {
      const ty = (h - padY) - ((target - minW) / range) * (h - 2 * padY);
      ctx.strokeStyle = '#F43F5E';
      ctx.setLineDash([4, 4]);
      ctx.beginPath(); ctx.moveTo(padX, ty); ctx.lineTo(w - padX, ty); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = '#F43F5E'; ctx.font = 'bold 11px Segoe UI'; ctx.textAlign = 'right';
      ctx.fillText('Цель: ' + target + ' кг', w - padX, ty - 5);
    }

    const stepX = (w - 2 * padX) / (valid.length - 1);
    ctx.strokeStyle = '#9AE61C'; ctx.lineWidth = 3; ctx.beginPath();
    const pts = valid.map((e, i) => {
      const x = padX + i * stepX;
      const y = (h - padY) - ((e.weight - minW) / range) * (h - 2 * padY);
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      return {x, y, w: e.weight};
    });
    ctx.stroke();

    pts.forEach(p => {
      ctx.fillStyle = '#F8FAFC';
      ctx.beginPath(); ctx.arc(p.x, p.y, 4, 0, Math.PI * 2); ctx.fill();
      ctx.font = 'bold 11px Segoe UI'; ctx.textAlign = 'center';
      ctx.fillText(p.w.toFixed(1), p.x, p.y - 8);
    });
  }

  refresh();
</script>
</body>
</html>
"""


class FitiaAppHandler(http.server.BaseHTTPRequestHandler):

    def _get_uid(self):
        init_data = self.headers.get("X-Telegram-Init-Data", "")
        fallback = self.headers.get("X-Fallback-User", "default_user")
        return extract_user_id(init_data, fallback)

    def _send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed_url = urllib.parse.urlparse(self.path)
        if parsed_url.path == "/api/state":
            user_id = self._get_uid()
            qs = urllib.parse.parse_qs(parsed_url.query)
            date_str = qs.get("date", [datetime.now().strftime("%Y-%m-%d")])[0]

            with sqlite3.connect(DB_PATH) as conn:
                cur = conn.cursor()
                settings = get_user_settings(cur, user_id)

                cur.execute(
                    "SELECT date, weight, calories, steps, note, water_ml FROM entries WHERE telegram_id = ? ORDER BY date ASC",
                    (user_id,),
                )
                entries = [
                    {
                        "date": r[0],
                        "weight": r[1],
                        "calories": r[2],
                        "steps": r[3],
                        "note": r[4],
                        "water_ml": r[5] or 0,
                    }
                    for r in cur.fetchall()
                ]

                cur.execute(
                    "SELECT water_ml FROM entries WHERE telegram_id = ? AND date = ?",
                    (user_id, date_str),
                )
                w_row = cur.fetchone()
                water_ml = w_row[0] if (w_row and w_row[0]) else 0

                cur.execute(
                    """SELECT id, meal_type, food_name, grams, calories, protein, fat, carbs
                       FROM meal_log WHERE telegram_id = ? AND date = ? ORDER BY id ASC""",
                    (user_id, date_str),
                )
                day_meals = [
                    {
                        "id": r[0],
                        "meal_type": r[1] or "breakfast",
                        "food_name": r[2],
                        "grams": r[3],
                        "calories": r[4],
                        "p": r[5] or 0,
                        "f": r[6] or 0,
                        "c": r[7] or 0,
                    }
                    for r in cur.fetchall()
                ]

                cur.execute(
                    """SELECT id, workout_name, duration_min, calories_burned
                       FROM workout_log WHERE telegram_id = ? AND date = ? ORDER BY id DESC""",
                    (user_id, date_str),
                )
                day_workouts = [
                    {"id": r[0], "name": r[1], "mins": r[2], "cal": r[3]}
                    for r in cur.fetchall()
                ]

            valid = [e for e in entries if e["weight"] > 0]
            if valid:
                curr = valid[-1]["weight"]
                start = valid[0]["weight"]
                diff = curr - start
                sign = "+" if diff > 0 else ""
                curr_w = f"{curr:.1f} кг"
                rem_w = curr - settings["target_weight"]
                sub = (
                    f"Со старта: {sign}{diff:.1f} кг | До цели: {rem_w:.1f} кг"
                )
            else:
                curr_w = "-- кг"
                sub = f"Цель: {settings['target_weight']} кг"

            self._send_json(
                {
                    "settings": settings,
                    "curr_weight": curr_w,
                    "weight_sub": sub,
                    "water_ml": water_ml,
                    "today_cal": int(
                        round(sum(m["calories"] for m in day_meals))
                    ),
                    "today_p": round(sum(m["p"] for m in day_meals), 1),
                    "today_f": round(sum(m["f"] for m in day_meals), 1),
                    "today_c": round(sum(m["c"] for m in day_meals), 1),
                    "today_burn": int(
                        round(sum(w["cal"] for w in day_workouts))
                    ),
                    "day_meals": day_meals,
                    "day_workouts": day_workouts,
                    "entries": entries,
                    "foods": [
                        {
                            "name": f[0],
                            "cal": f[1],
                            "p": f[2],
                            "f": f[3],
                            "c": f[4],
                        }
                        for f in DEFAULT_FOODS
                    ],
                    "workouts": [
                        {"name": w[0], "cal_min": w[1]}
                        for w in DEFAULT_WORKOUTS
                    ],
                }
            )
        else:
            body = WEB_APP_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def do_POST(self):
        user_id = self._get_uid()
        length = int(self.headers.get("Content-Length", 0))
        data = json.loads(self.rfile.read(length).decode("utf-8"))

        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()

            if self.path == "/api/save_goals":
                for k in (
                    "target_weight",
                    "target_cal",
                    "target_p",
                    "target_f",
                    "target_c",
                    "target_water",
                ):
                    if k in data:
                        save_setting(cur, user_id, k, data[k])

            elif self.path == "/api/add_water":
                date = data["date"]
                delta = int(data.get("delta", 250))
                cur.execute(
                    "SELECT water_ml FROM entries WHERE telegram_id = ? AND date = ?",
                    (user_id, date),
                )
                row = cur.fetchone()
                curr_w = row[0] if (row and row[0]) else 0
                new_w = max(0, curr_w + delta)
                cur.execute(
                    """INSERT INTO entries (telegram_id, date, weight, calories, steps, note, water_ml)
                       VALUES (?, ?, 0, 0, 0, '', ?)
                       ON CONFLICT(telegram_id, date) DO UPDATE SET water_ml = excluded.water_ml""",
                    (user_id, date, new_w),
                )

            elif self.path == "/api/add_meal":
                date = data["date"]
                cur.execute(
                    """INSERT INTO meal_log (telegram_id, date, meal_type, food_name, grams, calories, protein, fat, carbs)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        user_id,
                        date,
                        data.get("meal_type", "breakfast"),
                        data["name"],
                        float(data["grams"]),
                        float(data["cal"]),
                        float(data.get("p", 0)),
                        float(data.get("f", 0)),
                        float(data.get("c", 0)),
                    ),
                )
                sync_daily_calories(cur, user_id, date)

            elif self.path == "/api/delete_meal":
                cur.execute(
                    "DELETE FROM meal_log WHERE id = ? AND telegram_id = ?",
                    (int(data["id"]), user_id),
                )
                sync_daily_calories(cur, user_id, data["date"])

            elif self.path == "/api/clear_day_meals":
                date = data["date"]
                cur.execute(
                    "DELETE FROM meal_log WHERE telegram_id = ? AND date = ?",
                    (user_id, date),
                )
                sync_daily_calories(cur, user_id, date)

            elif self.path == "/api/generate_day":
                date = data["date"]
                settings = get_user_settings(cur, user_id)
                target_cal = settings["target_cal"]
                food_map = {f[0]: f for f in DEFAULT_FOODS}

                cur.execute(
                    "DELETE FROM meal_log WHERE telegram_id = ? AND date = ?",
                    (user_id, date),
                )

                shares = {
                    "breakfast": 0.25,
                    "lunch": 0.35,
                    "dinner": 0.25,
                    "snack": 0.15,
                }
                for m_type, m_share in shares.items():
                    meal_cal_budget = target_cal * m_share
                    chosen_combo = random.choice(MEAL_TEMPLATES[m_type])
                    for f_name, item_share in chosen_combo:
                        if f_name not in food_map:
                            continue
                        _, cal100, p100, f100, c100 = food_map[f_name]
                        item_cal = meal_cal_budget * item_share
                        grams = round((item_cal / cal100) * 100 / 10) * 10
                        grams = max(grams, 50)
                        fac = grams / 100.0
                        cur.execute(
                            """INSERT INTO meal_log (telegram_id, date, meal_type, food_name, grams, calories, protein, fat, carbs)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                user_id,
                                date,
                                m_type,
                                f_name,
                                grams,
                                round(cal100 * fac, 1),
                                round(p100 * fac, 1),
                                round(f100 * fac, 1),
                                round(c100 * fac, 1),
                            ),
                        )
                sync_daily_calories(cur, user_id, date)

            elif self.path == "/api/add_entry":
                date = data["date"]
                weight = float(data.get("weight", 0))
                steps = int(data.get("steps", 0))
                note = data.get("note", "")
                cur.execute(
                    """INSERT INTO entries (telegram_id, date, weight, calories, steps, note, water_ml)
                       VALUES (?, ?, ?, 0, ?, ?, 0)
                       ON CONFLICT(telegram_id, date) DO UPDATE SET
                           weight = CASE WHEN excluded.weight > 0 THEN excluded.weight ELSE entries.weight END,
                           steps = CASE WHEN excluded.steps > 0 THEN excluded.steps ELSE entries.steps END,
                           note = CASE WHEN excluded.note != '' THEN excluded.note ELSE entries.note END""",
                    (user_id, date, weight, steps, note),
                )

            elif self.path == "/api/delete_entry":
                cur.execute(
                    "DELETE FROM entries WHERE telegram_id = ? AND date = ?",
                    (user_id, data["date"]),
                )

            elif self.path == "/api/add_workout":
                cur.execute(
                    """INSERT INTO workout_log (telegram_id, date, workout_name, duration_min, calories_burned)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        user_id,
                        data["date"],
                        data["name"],
                        float(data["mins"]),
                        float(data["cal"]),
                    ),
                )

            elif self.path == "/api/delete_workout":
                cur.execute(
                    "DELETE FROM workout_log WHERE id = ? AND telegram_id = ?",
                    (int(data["id"]), user_id),
                )

            conn.commit()

        self._send_json({"ok": True})

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    init_db()
    server = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), FitiaAppHandler)
    print(f"Fit Studio 2026 (Fitia Edition) running on port {PORT}")
    server.serve_forever()
