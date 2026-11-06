from datetime import datetime, timedelta
import http.server
import json
import os
import random
import sqlite3
import urllib.parse

PORT = int(os.environ.get("PORT", 8000))
DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "grammika_db.db"
)

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
        cur.execute(
            """CREATE TABLE IF NOT EXISTS custom_foods (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id TEXT,
                name TEXT,
                calories REAL,
                protein REAL,
                fat REAL,
                carbs REAL
            )"""
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
        "onboarded": rows.get("onboarded", "0"),
        "target_weight": float(rows.get("target_weight", 73.0)),
        "target_cal": int(float(rows.get("target_cal", 1850))),
        "target_p": int(float(rows.get("target_p", 138))),
        "target_f": int(float(rows.get("target_f", 62))),
        "target_c": int(float(rows.get("target_c", 185))),
        "target_water": int(float(rows.get("target_water", 2250))),
        "excluded": json.loads(rows.get("excluded", "[]")),
    }


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
<title>Grammika — Умный трекер питания</title>
<style>
  :root {
    --bg: #0B0F0C;
    --card: #131B15;
    --card2: #1A261E;
    --input: #1F2E23;
    --accent: #A3FF12;
    --text: #F1F5F9;
    --muted: #8FA895;
    --warn: #FF3B5C;
    --blue: #38BDF8;
    --amber: #FBBF24;
    --green: #22C55E;
    --border: #233827;
    --transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
  }
  * { box-sizing: border-box; font-family: 'Segoe UI', system-ui, -apple-system, sans-serif; -webkit-tap-highlight-color: transparent; }
  body { margin: 0; padding: 12px 12px 86px; background: var(--bg); color: var(--text); transition: var(--transition); }
  .container { max-width: 580px; margin: 0 auto; }

  .onboard-backdrop, .modal-backdrop { 
    position: fixed; inset: 0; background: rgba(5,8,6,0.88); z-index: 1000; 
    display: none; align-items: center; justify-content: center; padding: 16px; 
    backdrop-filter: blur(8px); opacity: 0; transition: opacity 0.3s ease;
  }
  .onboard-backdrop.open, .modal-backdrop.open { display: flex; opacity: 1; }
  
  .onboard-card, .modal-card { 
    background: var(--card); color: var(--text); width: 100%; max-width: 440px; 
    border-radius: 24px; padding: 24px; border: 1px solid var(--border); 
    box-shadow: 0 25px 50px rgba(0,0,0,0.6);
    transform: translateY(20px) scale(0.95); transition: transform 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
  }
  .onboard-backdrop.open .onboard-card, .modal-backdrop.open .modal-card { 
    transform: translateY(0) scale(1); 
  }
  .onboard-card h2, .modal-top h3 { margin: 0 0 6px; color: var(--accent); font-size: 20px; font-weight: 800; }
  .onboard-card p { font-size: 13px; color: var(--muted); margin-bottom: 16px; }

  .top-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; gap: 8px; }
  .brand-title { font-size: 20px; font-weight: 900; letter-spacing: -0.5px; display: flex; align-items: center; gap: 8px; color: var(--accent); }
  .date-pill { display: flex; align-items: center; background: var(--card); border: 1px solid var(--border); color: var(--text); padding: 6px 14px; border-radius: 20px; gap: 6px; box-shadow: 0 4px 12px rgba(0,0,0,0.3); }
  .date-pill input { background: transparent; border: none; color: var(--accent); font-weight: 800; font-size: 13px; width: 112px; outline: none; }

  .fitia-hero { background: var(--card); border: 1px solid var(--border); color: var(--text); border-radius: 24px; padding: 18px; margin-bottom: 14px; box-shadow: 0 10px 30px rgba(0,0,0,0.3); }
  .cal-summary { display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; text-align: center; gap: 8px; margin-bottom: 16px; }
  .cal-stat .num { font-size: 19px; font-weight: 800; }
  .cal-stat .lbl { font-size: 11px; color: var(--muted); }

  .ring-box { position: relative; width: 120px; height: 120px; display: flex; flex-direction: column; align-items: center; justify-content: center; border-radius: 50%; border: 7px solid var(--input); transition: var(--transition); box-shadow: inset 0 0 15px rgba(0,0,0,0.5); }
  .ring-box.active { border-color: var(--accent); box-shadow: 0 0 20px rgba(163,255,18,0.2), inset 0 0 15px rgba(0,0,0,0.5); }
  .ring-box.over { border-color: var(--warn); box-shadow: 0 0 20px rgba(255,59,92,0.3), inset 0 0 15px rgba(0,0,0,0.5); }
  .ring-val { font-size: 25px; font-weight: 900; line-height: 1; color: var(--accent); }
  .ring-lbl { font-size: 10px; color: var(--muted); margin-top: 3px; text-transform: uppercase; font-weight: 700; }

  .macros-row { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
  .macro-card { background: var(--card2); border: 1px solid var(--border); padding: 10px; border-radius: 14px; }
  .macro-head { display: flex; justify-content: space-between; font-size: 11px; font-weight: 700; margin-bottom: 6px; }
  .p-bar-bg { width: 100%; height: 7px; background: var(--input); border-radius: 6px; overflow: hidden; }
  .p-bar-fill { height: 100%; width: 0%; border-radius: 6px; transition: width 0.5s cubic-bezier(0.4, 0, 0.2, 1); }
  .macro-sub { font-size: 11px; color: var(--muted); margin-top: 5px; text-align: right; font-weight: 600; }

  .ai-banner { background: linear-gradient(135deg, #16261A 0%, #111A13 100%); border: 1px solid var(--border); color: var(--text); border-radius: 20px; padding: 14px 16px; margin-bottom: 14px; display: flex; justify-content: space-between; align-items: center; gap: 10px; box-shadow: 0 6px 20px rgba(0,0,0,0.2); }
  .ai-banner h4 { margin: 0 0 2px; font-size: 14px; color: var(--accent); }
  .ai-banner p { margin: 0; font-size: 11px; color: var(--muted); }
  .ai-btn { background: var(--accent); color: var(--bg); border: none; padding: 10px 14px; border-radius: 12px; font-weight: 900; font-size: 12px; cursor: pointer; white-space: nowrap; transition: transform 0.15s ease, opacity 0.15s ease; }
  .ai-btn:active { transform: scale(0.95); }

  .water-card { background: var(--card); border: 1px solid var(--border); color: var(--text); border-radius: 20px; padding: 15px 18px; margin-bottom: 14px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; box-shadow: 0 6px 20px rgba(0,0,0,0.2); }
  .water-info h4 { margin: 0; font-size: 13px; color: var(--blue); }
  .water-info .w-val { font-size: 16px; font-weight: 800; margin-top: 2px; }
  .water-glasses { display: flex; gap: 4px; font-size: 16px; margin-top: 5px; }
  .water-btns { display: flex; gap: 6px; }
  .water-btns button { background: var(--input); color: var(--blue); border: 1px solid rgba(56,189,248,0.3); padding: 7px 12px; border-radius: 10px; font-weight: 800; font-size: 12px; cursor: pointer; transition: transform 0.15s ease; }
  .water-btns button:active { transform: scale(0.93); }

  .meal-card { background: var(--card); border: 1px solid var(--border); color: var(--text); border-radius: 20px; padding: 16px; margin-bottom: 12px; box-shadow: 0 6px 20px rgba(0,0,0,0.2); }
  .meal-header { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--input); padding-bottom: 8px; margin-bottom: 8px; }
  .meal-title { font-size: 15px; font-weight: 800; display: flex; align-items: center; gap: 6px; }
  .meal-kcal { font-size: 13px; color: var(--accent); font-weight: 800; }
  .meal-add-btn { background: var(--accent); color: var(--bg); border: none; border-radius: 10px; padding: 6px 12px; font-weight: 900; font-size: 12px; cursor: pointer; transition: transform 0.15s ease; }
  .meal-add-btn:active { transform: scale(0.95); }

  .food-item { display: flex; justify-content: space-between; align-items: center; padding: 7px 0; border-bottom: 1px dashed var(--input); font-size: 13px; transition: opacity 0.2s ease; }
  .food-item:last-child { border-bottom: none; }
  .food-name { font-weight: 600; }
  .food-meta { font-size: 11px; color: var(--muted); }
  .food-right { display: flex; align-items: center; gap: 8px; }
  .food-cal { font-weight: 800; color: var(--text); font-size: 13px; }
  .btn-group { display: flex; gap: 4px; }
  .action-btn { background: var(--input); color: var(--accent); border: 1px solid var(--border); border-radius: 6px; padding: 4px 7px; font-size: 11px; font-weight: 700; cursor: pointer; transition: transform 0.1s ease; }
  .action-btn:active { transform: scale(0.9); }
  .del-btn { background: rgba(255,59,92,0.15); color: var(--warn); border: none; border-radius: 6px; width: 24px; height: 24px; font-weight: 800; cursor: pointer; display: flex; align-items: center; justify-content: center; }

  .tab-view { display: none; opacity: 0; transform: translateY(8px); transition: opacity 0.3s ease, transform 0.3s ease; }
  .tab-view.active { display: block; opacity: 1; transform: translateY(0); }
  
  .box-card { background: var(--card); border: 1px solid var(--border); color: var(--text); border-radius: 20px; padding: 18px; margin-bottom: 14px; box-shadow: 0 6px 20px rgba(0,0,0,0.2); }
  .box-card h3 { margin: 0 0 10px; color: var(--accent); font-size: 15px; }
  
  input, select { width: 100%; padding: 11px; margin: 6px 0; border-radius: 12px; border: 1px solid var(--border); background: var(--input); color: var(--text); font-size: 13px; outline: none; transition: border-color 0.2s ease; }
  input:focus, select:focus { border-color: var(--accent); }
  
  .btn-main { width: 100%; padding: 12px; margin-top: 10px; border-radius: 12px; border: none; background: var(--accent); color: var(--bg); font-weight: 900; font-size: 14px; cursor: pointer; transition: transform 0.15s ease, opacity 0.15s ease; box-shadow: 0 4px 15px rgba(163,255,18,0.2); }
  .btn-main:active { transform: scale(0.97); opacity: 0.9; }
  .btn-danger { background: var(--warn); color: var(--text); box-shadow: 0 4px 15px rgba(255,59,92,0.2); }
  
  .row-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
  .row-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; }

  .shop-item { display: flex; justify-content: space-between; align-items: center; padding: 9px 0; border-bottom: 1px solid var(--input); font-size: 13px; cursor: pointer; transition: opacity 0.2s ease, text-decoration 0.2s ease; }
  .shop-item.checked { opacity: 0.35; text-decoration: line-through; }

  .bottom-nav { position: fixed; bottom: 0; left: 0; right: 0; background: rgba(19,27,21,0.95); border-top: 1px solid var(--border); display: flex; justify-content: space-around; padding: 9px 6px 14px; z-index: 100; backdrop-filter: blur(12px); }
  .nav-btn { background: transparent; border: none; color: var(--muted); font-size: 11px; font-weight: 700; display: flex; flex-direction: column; align-items: center; gap: 3px; cursor: pointer; width: 25%; transition: color 0.2s ease, transform 0.15s ease; }
  .nav-btn .ico { font-size: 18px; transition: transform 0.2s cubic-bezier(0.34, 1.56, 0.64, 1); }
  .nav-btn.active { color: var(--accent); }
  .nav-btn.active .ico { transform: scale(1.25) translateY(-2px); }

  canvas { width: 100%; height: 170px; background: var(--input); border-radius: 12px; margin-top: 8px; }
  table { width: 100%; border-collapse: collapse; margin-top: 8px; font-size: 12px; }
  th, td { text-align: left; padding: 8px 6px; border-bottom: 1px solid var(--input); }
  th { color: var(--accent); }
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
    <div style="display:flex; gap:12px; margin-top:4px; font-size:13px;">
      <label><input type="checkbox" value="fish" id="excFish"> Рыбу</label>
      <label><input type="checkbox" value="meat" id="excMeat"> Мясо/Говядину</label>
      <label><input type="checkbox" value="dairy" id="excDairy"> Молочку/Творог</label>
    </div>

    <button class="btn-main" style="margin-top:16px;" onclick="completeOnboarding()">🚀 Создать мой план питания</button>
  </div>
</div>

<div class="container">
  <div class="top-header">
    <div class="brand-title">✨ GRAMMIKA <span id="streakBadge" style="font-size:11px; background:var(--input); color:var(--accent); border:1px solid var(--border); padding:3px 9px; border-radius:12px;">🔥 Стрик: 0 дн.</span></div>
    <div class="date-pill">
      <span>📅</span>
      <input type="date" id="currentDate" onchange="refresh()">
    </div>
  </div>

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

  <!-- ВКЛАДКА 3: ВЕС И ШАГИ -->
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
      <h3>🏋️ Тренировка / Активность</h3>
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
      <canvas id="weightChart" width="520" height="180"></canvas>
    </div>

    <div class="box-card">
      <h3>📋 История</h3>
      <table id="historyTable">
        <thead><tr><th>Дата</th><th>Вес</th><th>Ккал</th><th>Шаги</th><th>Вода</th><th></th></tr></thead>
        <tbody></tbody>
      </table>
    </div>
  </div>

  <!-- ВКЛАДКА 4: ПРОФИЛЬ, РЕЦЕПТЫ И БЭКАП -->
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
      <button class="btn-main" style="background:var(--input); color:var(--accent); border:1px solid var(--border); margin-top:8px;" onclick="openOnboardAgain()">🔄 Повторить опросник / онбординг</button>
    </div>

    <div class="box-card">
      <h3>💾 Резервное копирование данных</h3>
      <button class="btn-main" style="background:var(--blue); color:var(--bg);" onclick="exportBackup()">📥 Скачать бэкап данных</button>
      <button class="btn-main" style="background:var(--input); color:var(--text); border:1px solid var(--border); margin-top:8px;" onclick="document.getElementById('importFile').click()">📤 Восстановить из бэкапа</button>
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
  <button class="nav-btn active" onclick="switchTab('diary', this)"><span class="ico">🥗</span><span>Дневник</span></button>
  <button class="nav-btn" onclick="switchTab('plan', this)"><span class="ico">✨</span><span>Авто-меню</span></button>
  <button class="nav-btn" onclick="switchTab('progress', this)"><span class="ico">📈</span><span>Вес & Шаги</span></button>
  <button class="nav-btn" onclick="switchTab('profile', this)"><span class="ico">🎯</span><span>Профиль</span></button>
</div>

<script>
  const tg = (window.Telegram && window.Telegram.WebApp) ? window.Telegram.WebApp : null;
  if (tg) { tg.expand(); }

  const initData = (tg && tg.initData) ? tg.initData : "";
  let tgUserId = "default_user";
  if (tg && tg.initDataUnsafe && tg.initDataUnsafe.user) {
    const u = tg.initDataUnsafe.user;
    tgUserId = String(u.id || "default_user");
  }

  const todayStr = new Date().toISOString().split('T')[0];
  document.getElementById('currentDate').value = todayStr;

  let foodCatalog = [];
  let workCatalog = [];
  let activeMealType = 'breakfast';
  let activeReplaceMealId = null;

  const MEAL_META = [
    { key: 'breakfast', title: '🌅 Завтрак', pct: 25 },
    { key: 'lunch',     title: '☀️ Обед',    pct: 35 },
    { key: 'dinner',    title: '🌙 Ужин',    pct: 25 },
    { key: 'snack',     title: '🍏 Перекус', pct: 15 }
  ];

  function switchTab(tabName, btn) {
    document.querySelectorAll('.tab-view').forEach(el => el.classList.remove('active'));
    const target = document.getElementById('tab-' + tabName);
    target.classList.add('active');
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

    await apiFetch('/api/save_goals', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        onboarded: "1",
        target_weight: tw,
        target_cal: targetCal,
        target_p: targetP,
        target_f: targetF,
        target_c: targetC,
        target_water: targetWater,
        excluded: excluded
      })
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

    const res = await apiFetch('/api/add_custom_food', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name, cal, p, f, c})
    });
    if (res.ok) {
      alert('Рецепт успешно сохранен в ваш каталог!');
      document.getElementById('customName').value = '';
      document.getElementById('customCal').value = '';
      document.getElementById('customP').value = '';
      document.getElementById('customF').value = '';
      document.getElementById('customC').value = '';
      refresh();
    }
  }

  async function exportBackup() {
    const data = await apiFetch('/api/export_backup');
    const blob = new Blob([JSON.stringify(data, null, 2)], {type: 'application/json'});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `grammika_backup_${todayStr}.json`;
    a.click();
  }

  async function importBackup(event) {
    const file = event.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = async function(e) {
      try {
        const jsonContent = JSON.parse(e.target.result);
        const res = await apiFetch('/api/import_backup', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(jsonContent)
        });
        if (res.ok) {
          alert('Резервная копия успешно восстановлена!');
          refresh();
        } else {
          alert('Ошибка при импорте файла.');
        }
      } catch (err) {
        alert('Неверный формат JSON файла.');
      }
    };
    reader.readAsText(file);
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
    rSel.innerHTML = foodCatalog.map((f, i) => `<option value="${i}">${f.name} (${f.cal} ккал/100г)</option>`).join('');
    document.getElementById('replaceModal').classList.add('open');
  }

  function closeReplaceModal() {
    document.getElementById('replaceModal').classList.remove('open');
  }

  async function confirmReplace() {
    const idx = document.getElementById('replaceSelect').value;
    if (idx === "" || !foodCatalog[idx] || !activeReplaceMealId) return;
    const newFood = foodCatalog[idx];
    const date = document.getElementById('currentDate').value || todayStr;

    await apiFetch('/api/replace_meal', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id: activeReplaceMealId, date, new_name: newFood.name, cal100: newFood.cal, p100: newFood.p, f100: newFood.f, c100: newFood.c})
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

  async function refresh() {
    const date = document.getElementById('currentDate').value || todayStr;
    const d = await apiFetch('/api/state?date=' + encodeURIComponent(date));

    if (d.settings.onboarded !== "1") {
      document.getElementById('onboardModal').classList.add('open');
    }

    if (d.foods) {
      foodCatalog = d.foods;
      const fSel = document.getElementById('foodSelect');
      fSel.innerHTML = '<option value="">-- Выберите продукт или свой рецепт --</option>';
      foodCatalog.forEach((f, i) => {
        fSel.innerHTML += `<option value="${i}">${f.name} (${f.cal} ккал/100г)</option>`;
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
                <div class="btn-group">
                  <button class="action-btn" onclick="openReplaceModal(${item.id})">🔄</button>
                  <button class="del-btn" onclick="deleteMeal(${item.id})">✕</button>
                </div>
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

    const shopDiv = document.getElementById('shoppingList');
    if ((d.day_meals || []).length === 0) {
      shopDiv.innerHTML = '<span style="color:var(--muted)">Сгенерируйте меню на день, чтобы сформировать чек-лист.</span>';
    } else {
      const totals = {};
      d.day_meals.forEach(m => { totals[m.food_name] = (totals[m.food_name] || 0) + m.grams; });
      shopDiv.innerHTML = Object.entries(totals).map(([name, gr]) =>
        `<div class="shop-item" onclick="this.classList.toggle('checked')">
          <span>☑️ ${name}</span><b style="color:var(--accent)">${Math.round(gr)} г</b>
        </div>`
      ).join('');
    }

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

  async function saveManualGoals() {
    await apiFetch('/api/save_goals', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        target_weight: parseFloat(document.getElementById('cTargetWeight').value || 73),
        target_cal: parseInt(document.getElementById('gCal').value || 1850),
        target_water: parseInt(document.getElementById('gWater').value || 2250)
      })
    });
    refresh();
    switchTab('diary', document.querySelectorAll('.nav-btn')[0]);
  }

  refresh();
</script>
</body>
</html>
"""


class GrammikaAppHandler(http.server.BaseHTTPRequestHandler):

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
                    "SELECT DISTINCT date FROM entries WHERE telegram_id = ? AND (calories > 0 OR water_ml > 0 OR weight > 0) ORDER BY date DESC",
                    (user_id,),
                )
                dates = [r[0] for r in cur.fetchall()]
                streak = 0
                check_date = datetime.now()
                for i in range(30):
                    ds = check_date.strftime("%Y-%m-%d")
                    if ds in dates:
                        streak += 1
                        check_date -= timedelta(days=1)
                    elif i == 0:
                        check_date -= timedelta(days=1)
                        ds_y = check_date.strftime("%Y-%m-%d")
                        if ds_y in dates:
                            streak += 1
                        else:
                            break
                    else:
                        break

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

                cur.execute(
                    "SELECT name, calories, protein, fat, carbs FROM custom_foods WHERE telegram_id = ?",
                    (user_id,),
                )
                custom_foods = [
                    {
                        "name": r[0] + " (свой рецепт)",
                        "cal": r[1],
                        "p": r[2],
                        "f": r[3],
                        "c": r[4],
                        "cat": "custom",
                    }
                    for r in cur.fetchall()
                ]

            all_foods = [
                {
                    "name": f[0],
                    "cal": f[1],
                    "p": f[2],
                    "f": f[3],
                    "c": f[4],
                    "cat": f[5],
                }
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
                    "streak": max(streak, 1),
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
                    "foods": all_foods,
                    "workouts": [
                        {"name": w[0], "cal_min": w[1]}
                        for w in DEFAULT_WORKOUTS
                    ],
                }
            )
        elif parsed_url.path == "/api/export_backup":
            user_id = self._get_uid()
            with sqlite3.connect(DB_PATH) as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT key, value FROM settings WHERE telegram_id = ?",
                    (user_id,),
                )
                settings = cur.fetchall()
                cur.execute(
                    "SELECT date, weight, calories, steps, note, water_ml FROM entries WHERE telegram_id = ?",
                    (user_id,),
                )
                entries = cur.fetchall()
                cur.execute(
                    "SELECT date, meal_type, food_name, grams, calories, protein, fat, carbs FROM meal_log WHERE telegram_id = ?",
                    (user_id,),
                )
                meals = cur.fetchall()
                cur.execute(
                    "SELECT date, workout_name, duration_min, calories_burned FROM workout_log WHERE telegram_id = ?",
                    (user_id,),
                )
                workouts = cur.fetchall()
                cur.execute(
                    "SELECT name, calories, protein, fat, carbs FROM custom_foods WHERE telegram_id = ?",
                    (user_id,),
                )
                custom = cur.fetchall()

            self._send_json(
                {
                    "settings": settings,
                    "entries": entries,
                    "meals": meals,
                    "workouts": workouts,
                    "custom_foods": custom,
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
        raw_body = self.rfile.read(length).decode("utf-8")
        data = json.loads(raw_body) if raw_body else {}

        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()

            if self.path == "/api/import_backup":
                cur.execute(
                    "DELETE FROM settings WHERE telegram_id = ?", (user_id,)
                )
                cur.execute(
                    "DELETE FROM entries WHERE telegram_id = ?", (user_id,)
                )
                cur.execute(
                    "DELETE FROM meal_log WHERE telegram_id = ?", (user_id,)
                )
                cur.execute(
                    "DELETE FROM workout_log WHERE telegram_id = ?", (user_id,)
                )
                cur.execute(
                    "DELETE FROM custom_foods WHERE telegram_id = ?", (user_id,)
                )

                for k, v in data.get("settings", []):
                    cur.execute(
                        "INSERT INTO settings (telegram_id, key, value) VALUES (?, ?, ?)",
                        (user_id, k, v),
                    )
                for r in data.get("entries", []):
                    cur.execute(
                        "INSERT INTO entries (telegram_id, date, weight, calories, steps, note, water_ml) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (user_id, r[0], r[1], r[2], r[3], r[4], r[5]),
                    )
                for r in data.get("meals", []):
                    cur.execute(
                        "INSERT INTO meal_log (telegram_id, date, meal_type, food_name, grams, calories, protein, fat, carbs) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (user_id, r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7]),
                    )
                for r in data.get("workouts", []):
                    cur.execute(
                        "INSERT INTO workout_log (telegram_id, date, workout_name, duration_min, calories_burned) VALUES (?, ?, ?, ?, ?)",
                        (user_id, r[0], r[1], r[2], r[3], r[4]),
                    )
                for r in data.get("custom_foods", []):
                    cur.execute(
                        "INSERT INTO custom_foods (telegram_id, name, calories, protein, fat, carbs) VALUES (?, ?, ?, ?, ?, ?)",
                        (user_id, r[0], r[1], r[2], r[3], r[4]),
                    )

            elif self.path == "/api/add_custom_food":
                cur.execute(
                    "INSERT INTO custom_foods (telegram_id, name, calories, protein, fat, carbs) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        user_id,
                        data["name"],
                        float(data["cal"]),
                        float(data["p"]),
                        float(data["f"]),
                        float(data["c"]),
                    ),
                )

            elif self.path == "/api/replace_meal":
                meal_id = int(data["id"])
                date = data["date"]
                new_name = data["new_name"]
                cal100 = float(data["cal100"])
                p100 = float(data["p100"])
                f100 = float(data["f100"])
                c100 = float(data["c100"])

                cur.execute(
                    "SELECT grams FROM meal_log WHERE id = ? AND telegram_id = ?",
                    (meal_id, user_id),
                )
                mrow = cur.fetchone()
                if mrow:
                    grams = mrow[0]
                    fac = grams / 100.0
                    cur.execute(
                        """UPDATE meal_log SET food_name = ?, calories = ?, protein = ?, fat = ?, carbs = ?
                           WHERE id = ? AND telegram_id = ?""",
                        (
                            new_name,
                            round(cal100 * fac, 1),
                            round(p100 * fac, 1),
                            round(f100 * fac, 1),
                            round(c100 * fac, 1),
                            meal_id,
                            user_id,
                        ),
                    )
                    sync_daily_calories(cur, user_id, date)

            elif self.path == "/api/save_goals":
                for k, v in data.items():
                    val_to_save = (
                        json.dumps(v)
                        if isinstance(v, (list, dict))
                        else str(v)
                    )
                    cur.execute(
                        """INSERT INTO settings (telegram_id, key, value) VALUES (?, ?, ?)
                           ON CONFLICT(telegram_id, key) DO UPDATE SET value = excluded.value""",
                        (user_id, k, val_to_save),
                    )

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
                excluded = settings["excluded"]

                filtered_foods = [
                    f for f in DEFAULT_FOODS if f[5] not in excluded
                ]
                if not filtered_foods:
                    filtered_foods = DEFAULT_FOODS

                food_map = {f[0]: f for f in filtered_foods}

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
                    valid_templates = []
                    for tpl in MEAL_TEMPLATES[m_type]:
                        if all(item[0] in food_map for item in tpl):
                            valid_templates.append(tpl)
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
                       VALUES (?, ?, ?, ?, ?, ?, 0)
                       ON CONFLICT(telegram_id, date) DO UPDATE SET
                           weight = CASE WHEN excluded.weight > 0 THEN excluded.weight ELSE entries.weight END,
                           steps = CASE WHEN excluded.steps > 0 THEN excluded.steps ELSE entries.steps END,
                           note = CASE WHEN excluded.note != '' THEN excluded.note ELSE entries.note END""",
                    (user_id, date, weight, 0, steps, note),
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
    server = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), GrammikaAppHandler)
    print(f"Grammika Server running on port {PORT}")
    server.serve_forever()
