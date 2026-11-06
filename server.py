from datetime import datetime
import http.server
import json
import os
import sqlite3
import urllib.parse

PORT = int(os.environ.get("PORT", 8000))
DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "cloud_tracker_v2.db"
)

DEFAULT_FOODS = [
    ("Куриное филе (вареное/печеное)", 165, 31.0, 3.6, 0.0),
    ("Яйцо вареное (2 шт ~ 100г)", 155, 13.0, 11.0, 1.1),
    ("Творог 5%", 121, 17.0, 5.0, 3.0),
    ("Гречневая каша на воде", 110, 4.2, 1.1, 21.3),
    ("Рис белый вареный", 130, 2.7, 0.3, 28.0),
    ("Овсяная каша на воде", 88, 3.0, 1.7, 15.0),
    ("Макароны твердых сортов", 140, 5.5, 0.7, 28.0),
    ("Сыр твердый 45%", 350, 25.0, 28.0, 0.5),
    ("Хлеб цельнозерновой", 220, 7.0, 1.5, 43.0),
    ("Огурцы свежие", 15, 0.8, 0.1, 2.8),
    ("Помидоры свежие", 20, 0.9, 0.2, 3.9),
    ("Яблоко", 52, 0.3, 0.2, 13.8),
    ("Банан", 95, 1.5, 0.2, 21.8),
]

DEFAULT_WORKOUTS = [
    ("Быстрая прогулка / прогулка с собакой", 4.8),
    ("Спокойная пешая ходьба", 3.5),
    ("Приседания и выпады (интервалы)", 6.5),
    ("Отжимания и базовые упражнения", 6.0),
    ("Планка и пресс", 5.0),
    ("Комплексная разминка / зарядка", 3.2),
    ("Интенсивное кардио (HIIT)", 9.0),
]


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
                UNIQUE(telegram_id, date)
            )"""
        )
        cur.execute(
            """CREATE TABLE IF NOT EXISTS meal_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id TEXT,
                date TEXT,
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
        conn.commit()


def extract_user_id(init_data_str, fallback_id="default_user"):
    """Определяет ID пользователя Telegram или включает общий профиль без ошибки"""
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


WEB_APP_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<title>Fit Studio 2026 — Полная версия</title>
<style>
  :root {
    --lime: #C6F16D;
    --card: #14230D;
    --card-hover: #1E3514;
    --input: #223816;
    --accent: #9AE61C;
    --text: #F8FAFC;
    --muted: #8DAA7C;
    --warn: #F43F5E;
    --blue: #38BDF8;
    --amber: #F59E0B;
  }
  * { box-sizing: border-box; font-family: 'Segoe UI', system-ui, sans-serif; }
  body { margin: 0; padding: 14px; background: var(--lime); color: var(--card); }
  .top-bar { display: flex; justify-content: space-between; align-items: center; max-width: 980px; margin: 0 auto 12px; flex-wrap: wrap; gap: 8px; }
  .top-bar h1 { margin: 0; font-size: 20px; font-weight: 900; }
  .badge { background: var(--card); color: var(--accent); padding: 6px 12px; border-radius: 20px; font-weight: 700; font-size: 12px; }
  .target-box { display: flex; align-items: center; gap: 6px; background: var(--card); color: var(--text); padding: 4px 10px; border-radius: 12px; font-size: 12px; font-weight: 700; }
  .target-box input { width: 65px; padding: 5px; margin: 0; text-align: center; font-weight: 800; color: var(--accent); }
  .target-box button { width: auto; padding: 5px 10px; margin: 0; font-size: 12px; }

  .bento { display: grid; grid-template-columns: repeat(auto-fit, minmax(290px, 1fr)); gap: 12px; max-width: 980px; margin: 0 auto; }
  .puzzle { background: var(--card); color: var(--text); padding: 16px; border-radius: 16px; display: flex; flex-direction: column; justify-content: space-between; }
  .puzzle-wide { grid-column: 1 / -1; }
  .puzzle h3 { margin: 0 0 6px; color: var(--accent); font-size: 13px; letter-spacing: 0.4px; }
  .big-val { font-size: 24px; font-weight: 800; margin: 4px 0; }
  .sub { color: var(--muted); font-size: 12px; margin-bottom: 10px; }

  input, select { width: 100%; padding: 9px 10px; margin: 4px 0; border-radius: 8px; border: none; background: var(--input); color: var(--text); font-size: 13px; }
  button { width: 100%; padding: 10px; margin-top: 6px; border-radius: 8px; border: none; background: var(--accent); color: var(--card); font-weight: 800; font-size: 13px; cursor: pointer; }
  button:active { opacity: 0.85; }
  .btn-del { background: var(--warn); color: var(--text); padding: 4px 8px; font-size: 11px; width: auto; margin: 0; }

  canvas { width: 100%; height: 170px; background: var(--input); border-radius: 10px; margin-top: 6px; }
  table { width: 100%; border-collapse: collapse; margin-top: 8px; font-size: 12px; }
  th, td { text-align: left; padding: 7px 6px; border-bottom: 1px solid var(--input); }
  th { color: var(--accent); }
  .row-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  .row-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; }
</style>
</head>
<body>

  <div class="top-bar">
    <h1>🧩 FIT STUDIO 2026</h1>
    <div class="target-box">
      <span>Цель (кг):</span>
      <input type="number" step="0.1" id="targetInput" placeholder="73.0">
      <button onclick="saveTarget()">ОК</button>
    </div>
    <span class="badge" id="userBadge">🟢 Онлайн-режим</span>
  </div>

  <div class="bento">
    <!-- Пазл 1: Ввод веса и шагов -->
    <div class="puzzle">
      <div>
        <h3>📊 ДИНАМИКА ВЕСА И ШАГИ</h3>
        <div class="big-val" id="currWeight">-- кг</div>
        <div class="sub" id="weightSub">Загрузка...</div>
      </div>
      <div>
        <input type="date" id="entryDate">
        <div class="row-2">
          <input type="number" step="0.1" id="wInput" placeholder="Вес (кг)">
          <input type="number" id="sInput" placeholder="Шаги">
        </div>
        <input type="text" id="noteInput" placeholder="Заметка / самочувствие">
        <button onclick="sendEntry()">Сохранить запись дня</button>
      </div>
    </div>

    <!-- Пазл 2: Питание и каталог БЖУ -->
    <div class="puzzle">
      <div>
        <h3 style="color:var(--blue)">🍲 ДНЕВНИК ПИТАНИЯ & БЖУ</h3>
        <div class="big-val" id="calVal">0 ккал</div>
        <div class="sub" id="bjuSub">Б: 0г | Ж: 0г | У: 0г</div>
      </div>
      <div>
        <select id="foodSelect" onchange="onFoodSelect()"></select>
        <input type="text" id="fName" placeholder="Или введите свое блюдо">
        <div class="row-2">
          <input type="number" id="fGrams" placeholder="Граммы" value="150" oninput="onFoodSelect()">
          <input type="number" id="fCal" placeholder="Ккал порции">
        </div>
        <button onclick="sendMeal()">+ Добавить прием пищи</button>
      </div>
    </div>

    <!-- Пазл 3: Тренировки -->
    <div class="puzzle">
      <div>
        <h3 style="color:var(--warn)">🏋️ ТРЕНИРОВКИ & АКТИВНОСТЬ</h3>
        <div class="big-val" id="burnVal" style="color:var(--warn)">🔥 0 ккал</div>
        <div class="sub" id="workSub">Активность за сегодня: 0 мин</div>
      </div>
      <div>
        <select id="workSelect" onchange="onWorkSelect()"></select>
        <input type="text" id="wkName" placeholder="Или своя активность">
        <div class="row-2">
          <input type="number" id="wkMin" placeholder="Минуты" value="30" oninput="onWorkSelect()">
          <input type="number" id="wkCal" placeholder="Сожжено ккал">
        </div>
        <button onclick="sendWork()">+ Записать тренировку</button>
      </div>
    </div>

    <!-- Пазл 4: График прогресса -->
    <div class="puzzle puzzle-wide">
      <h3>📈 ГРАФИК СНИЖЕНИЯ ВЕСА</h3>
      <canvas id="weightChart" width="800" height="190"></canvas>
    </div>

    <!-- Пазл 5: Калькулятор калорий Миффлина - Сан Жеора -->
    <div class="puzzle">
      <div>
        <h3 style="color:var(--amber)">🧮 КАЛЬКУЛЯТОР ДЕФИЦИТА КАЛОРИЙ</h3>
        <div class="sub" id="calcOut">Введите параметры для расчета дневной нормы</div>
      </div>
      <div>
        <div class="row-3">
          <input type="number" id="cAge" placeholder="Возраст" value="29">
          <input type="number" id="cHeight" placeholder="Рост (см)" value="167">
          <input type="number" step="0.1" id="cWeight" placeholder="Вес (кг)" value="93">
        </div>
        <select id="cAct">
          <option value="1.2">Сидячая работа за ПК (1.2)</option>
          <option value="1.375">Прогулки / легкая активность (1.375)</option>
          <option value="1.55">Тренировки 3-5 раз в неделю (1.55)</option>
        </select>
        <button onclick="calcTDEE()">Рассчитать норму</button>
      </div>
    </div>

    <!-- Пазл 6: История записей -->
    <div class="puzzle" style="grid-column: span 2;">
      <h3>📋 ИСТОРИЯ ДНЕЙ</h3>
      <table id="historyTable">
        <thead>
          <tr><th>Дата</th><th>Вес</th><th>Ккал</th><th>Шаги</th><th>Заметка</th><th></th></tr>
        </thead>
        <tbody></tbody>
      </table>
    </div>
  </div>

<script>
  const tg = (window.Telegram && window.Telegram.WebApp) ? window.Telegram.WebApp : null;
  if (tg) { tg.expand(); }

  const initData = (tg && tg.initData) ? tg.initData : "";
  let tgUserId = "default_user";
  if (tg && tg.initDataUnsafe && tg.initDataUnsafe.user) {
    const u = tg.initDataUnsafe.user;
    tgUserId = String(u.id || "default_user");
    document.getElementById('userBadge').innerText = "👤 " + (u.first_name || "Telegram");
  }

  const todayStr = new Date().toISOString().split('T')[0];
  document.getElementById('entryDate').value = todayStr;

  let foodCatalog = [];
  let workCatalog = [];

  async function apiFetch(url, options = {}) {
    options.headers = options.headers || {};
    options.headers['X-Telegram-Init-Data'] = initData;
    options.headers['X-Fallback-User'] = tgUserId;
    const res = await fetch(url, options);
    return res.json();
  }

  function onFoodSelect() {
    const idx = document.getElementById('foodSelect').value;
    const grams = parseFloat(document.getElementById('fGrams').value || 100);
    if (idx !== "" && foodCatalog[idx]) {
      const item = foodCatalog[idx];
      document.getElementById('fName').value = item.name;
      document.getElementById('fCal').value = Math.round((item.cal * grams) / 100);
    }
  }

  function onWorkSelect() {
    const idx = document.getElementById('workSelect').value;
    const mins = parseFloat(document.getElementById('wkMin').value || 30);
    if (idx !== "" && workCatalog[idx]) {
      const item = workCatalog[idx];
      document.getElementById('wkName').value = item.name;
      document.getElementById('wkCal').value = Math.round(item.cal_min * mins);
    }
  }

  async function refresh() {
    const d = await apiFetch('/api/state');
    document.getElementById('currWeight').innerText = d.curr_weight;
    document.getElementById('weightSub').innerText = d.weight_sub;
    if (d.target_weight) {
      document.getElementById('targetInput').value = d.target_weight;
    }

    document.getElementById('calVal').innerText = d.today_cal + ' ккал';
    document.getElementById('bjuSub').innerText =
      `Приемов: ${d.meal_count} | Б: ${d.today_p}г | Ж: ${d.today_f}г | У: ${d.today_c}г`;

    document.getElementById('burnVal').innerText = '🔥 ' + d.today_burn + ' ккал';
    document.getElementById('workSub').innerText = `Активность за сегодня: ${d.today_min} мин`;

    if (foodCatalog.length === 0 && d.foods) {
      foodCatalog = d.foods;
      const fSel = document.getElementById('foodSelect');
      fSel.innerHTML = '<option value="">-- Выберите продукт из базы --</option>';
      foodCatalog.forEach((f, i) => {
        fSel.innerHTML += `<option value="${i}">${f.name} (${f.cal} ккал/100г)</option>`;
      });
    }

    if (workCatalog.length === 0 && d.workouts) {
      workCatalog = d.workouts;
      const wSel = document.getElementById('workSelect');
      wSel.innerHTML = '<option value="">-- Выберите тренировку из базы --</option>';
      workCatalog.forEach((w, i) => {
        wSel.innerHTML += `<option value="${i}">${w.name} (${w.cal_min} ккал/мин)</option>`;
      });
    }

    const tbody = document.querySelector('#historyTable tbody');
    tbody.innerHTML = '';
    (d.entries || []).slice().reverse().slice(0, 10).forEach(e => {
      tbody.innerHTML += `<tr>
        <td>${e.date}</td>
        <td><b>${e.weight} кг</b></td>
        <td>${e.calories}</td>
        <td>${e.steps}</td>
        <td>${e.note || '-'}</td>
        <td><button class="btn-del" onclick="deleteEntry('${e.date}')">✕</button></td>
      </tr>`;
    });

    drawChart(d.entries || [], parseFloat(d.target_weight || 0));
  }

  function drawChart(entries, target) {
    const canvas = document.getElementById('weightChart');
    const ctx = canvas.getContext('2d');
    const w = canvas.width;
    const h = canvas.height;
    ctx.clearRect(0, 0, w, h);

    const valid = entries.filter(e => e.weight > 0);
    if (valid.length < 2) {
      ctx.fillStyle = '#8DAA7C';
      ctx.font = '14px Segoe UI';
      ctx.textAlign = 'center';
      ctx.fillText('Добавьте минимум 2 записи веса за разные дни для построения графика', w / 2, h / 2);
      return;
    }

    const weights = valid.map(e => e.weight);
    let minW = Math.min(...weights);
    let maxW = Math.max(...weights);
    if (target > 0) {
      minW = Math.min(minW, target);
      maxW = Math.max(maxW, target);
    }
    minW -= 1.5;
    maxW += 1.5;
    const range = Math.max(maxW - minW, 1);
    const padX = 45, padY = 25;

    if (target > 0) {
      const ty = (h - padY) - ((target - minW) / range) * (h - 2 * padY);
      ctx.strokeStyle = '#F43F5E';
      ctx.setLineDash([5, 4]);
      ctx.beginPath();
      ctx.moveTo(padX, ty);
      ctx.lineTo(w - padX, ty);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = '#F43F5E';
      ctx.font = 'bold 11px Segoe UI';
      ctx.textAlign = 'right';
      ctx.fillText('Цель: ' + target + ' кг', w - padX, ty - 5);
    }

    const stepX = (w - 2 * padX) / (valid.length - 1);
    ctx.strokeStyle = '#9AE61C';
    ctx.lineWidth = 3;
    ctx.beginPath();
    const pts = valid.map((e, i) => {
      const x = padX + i * stepX;
      const y = (h - padY) - ((e.weight - minW) / range) * (h - 2 * padY);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
      return {x, y, w: e.weight, d: e.date};
    });
    ctx.stroke();

    pts.forEach(p => {
      ctx.fillStyle = '#F8FAFC';
      ctx.beginPath();
      ctx.arc(p.x, p.y, 4, 0, Math.PI * 2);
      ctx.fill();
      ctx.font = 'bold 11px Segoe UI';
      ctx.textAlign = 'center';
      ctx.fillText(p.w.toFixed(1), p.x, p.y - 8);
    });
  }

  async function saveTarget() {
    const val = document.getElementById('targetInput').value;
    await apiFetch('/api/set_target', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({target: val})
    });
    refresh();
  }

  async function sendEntry() {
    const date = document.getElementById('entryDate').value || todayStr;
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

  async function sendMeal() {
    const name = document.getElementById('fName').value.trim();
    const grams = parseFloat(document.getElementById('fGrams').value || 100);
    const cal = parseFloat(document.getElementById('fCal').value || 0);
    const idx = document.getElementById('foodSelect').value;
    let p = 0, f = 0, c = 0;
    if (idx !== "" && foodCatalog[idx]) {
      const item = foodCatalog[idx];
      p = (item.p * grams) / 100.0;
      f = (item.f * grams) / 100.0;
      c = (item.c * grams) / 100.0;
    }
    if (!name) return;
    await apiFetch('/api/add_meal', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({date: todayStr, name, grams, cal, p, f, c})
    });
    document.getElementById('fName').value = '';
    document.getElementById('fCal').value = '';
    document.getElementById('foodSelect').value = '';
    refresh();
  }

  async function sendWork() {
    const name = document.getElementById('wkName').value.trim();
    const mins = parseFloat(document.getElementById('wkMin').value || 30);
    const cal = parseFloat(document.getElementById('wkCal').value || 100);
    if (!name) return;
    await apiFetch('/api/add_workout', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({date: todayStr, name, mins, cal})
    });
    document.getElementById('wkName').value = '';
    document.getElementById('workSelect').value = '';
    refresh();
  }

  function calcTDEE() {
    const age = parseFloat(document.getElementById('cAge').value || 29);
    const h = parseFloat(document.getElementById('cHeight').value || 167);
    const w = parseFloat(document.getElementById('cWeight').value || 93);
    const act = parseFloat(document.getElementById('cAct').value || 1.2);
    const bmr = (10 * w) + (6.25 * h) - (5 * age) + 5;
    const tdee = bmr * act;
    document.getElementById('calcOut').innerHTML =
      `Базовый обмен (BMR): <b>${Math.round(bmr)} ккал</b> | Поддержание: <b>${Math.round(tdee)} ккал</b><br>` +
      `🎯 Для похудения (-15% / -20%): <b style="color:var(--accent)">${Math.round(tdee*0.8)} – ${Math.round(tdee*0.85)} ккал/день</b>`;
  }

  refresh();
</script>
</body>
</html>
"""


class UniversalAppHandler(http.server.BaseHTTPRequestHandler):

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
        if self.path == "/api/state":
            user_id = self._get_uid()
            today = datetime.now().strftime("%Y-%m-%d")

            with sqlite3.connect(DB_PATH) as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT value FROM settings WHERE telegram_id = ? AND key = 'target_weight'",
                    (user_id,),
                )
                row = cur.fetchone()
                target_w = row[0] if row else ""

                cur.execute(
                    "SELECT date, weight, calories, steps, note FROM entries WHERE telegram_id = ? ORDER BY date ASC",
                    (user_id,),
                )
                entries = [
                    {
                        "date": r[0],
                        "weight": r[1],
                        "calories": r[2],
                        "steps": r[3],
                        "note": r[4],
                    }
                    for r in cur.fetchall()
                ]

                cur.execute(
                    "SELECT calories, protein, fat, carbs FROM meal_log WHERE telegram_id = ? AND date = ?",
                    (user_id, today),
                )
                meals = cur.fetchall()

                cur.execute(
                    "SELECT duration_min, calories_burned FROM workout_log WHERE telegram_id = ? AND date = ?",
                    (user_id, today),
                )
                workouts = cur.fetchall()

            valid = [e for e in entries if e["weight"] > 0]
            if valid:
                curr = valid[-1]["weight"]
                start = valid[0]["weight"]
                diff = curr - start
                sign = "+" if diff > 0 else ""
                curr_w = f"{curr:.1f} кг"
                sub = f"Динамика со старта: {sign}{diff:.1f} кг"
                if target_w:
                    try:
                        rem = curr - float(target_w)
                        sub += f" | До цели: {rem:.1f} кг"
                    except ValueError:
                        pass
            else:
                curr_w = "-- кг"
                sub = "Добавьте первую утреннюю отметку веса"

            today_cal = sum(m[0] for m in meals)
            today_p = sum(m[1] for m in meals)
            today_f = sum(m[2] for m in meals)
            today_c = sum(m[3] for m in meals)
            today_burn = sum(w[1] for w in workouts)
            today_min = sum(w[0] for w in workouts)

            self._send_json(
                {
                    "curr_weight": curr_w,
                    "weight_sub": sub,
                    "target_weight": target_w,
                    "today_cal": int(round(today_cal)),
                    "today_p": int(round(today_p)),
                    "today_f": int(round(today_f)),
                    "today_c": int(round(today_c)),
                    "meal_count": len(meals),
                    "today_burn": int(round(today_burn)),
                    "today_min": int(round(today_min)),
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

            if self.path == "/api/set_target":
                target = str(data.get("target", "")).replace(",", ".").strip()
                cur.execute(
                    """INSERT INTO settings (telegram_id, key, value) VALUES (?, 'target_weight', ?)
                       ON CONFLICT(telegram_id, key) DO UPDATE SET value = excluded.value""",
                    (user_id, target),
                )

            elif self.path == "/api/add_entry":
                date = data.get("date")
                weight = float(data.get("weight", 0))
                steps = int(data.get("steps", 0))
                note = data.get("note", "")
                cur.execute(
                    """INSERT INTO entries (telegram_id, date, weight, calories, steps, note)
                       VALUES (?, ?, ?, 0, ?, ?)
                       ON CONFLICT(telegram_id, date) DO UPDATE SET
                           weight = CASE WHEN excluded.weight > 0 THEN excluded.weight ELSE entries.weight END,
                           steps = CASE WHEN excluded.steps > 0 THEN excluded.steps ELSE entries.steps END,
                           note = CASE WHEN excluded.note != '' THEN excluded.note ELSE entries.note END""",
                    (user_id, date, weight, steps, note),
                )

            elif self.path == "/api/delete_entry":
                cur.execute(
                    "DELETE FROM entries WHERE telegram_id = ? AND date = ?",
                    (user_id, data.get("date")),
                )

            elif self.path == "/api/add_meal":
                date = data["date"]
                cal = float(data["cal"])
                cur.execute(
                    """INSERT INTO meal_log (telegram_id, date, food_name, grams, calories, protein, fat, carbs)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        user_id,
                        date,
                        data["name"],
                        float(data["grams"]),
                        cal,
                        float(data.get("p", 0)),
                        float(data.get("f", 0)),
                        float(data.get("c", 0)),
                    ),
                )
                # Автоматически обновляем калории в сводке дня
                cur.execute(
                    "SELECT SUM(calories) FROM meal_log WHERE telegram_id = ? AND date = ?",
                    (user_id, date),
                )
                total_cal = int(round(cur.fetchone()[0] or 0))
                cur.execute(
                    """INSERT INTO entries (telegram_id, date, weight, calories, steps, note)
                       VALUES (?, ?, 0, ?, 0, '')
                       ON CONFLICT(telegram_id, date) DO UPDATE SET calories = excluded.calories""",
                    (user_id, date, total_cal),
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

            conn.commit()

        self._send_json({"ok": True})

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    init_db()
    server = http.server.ThreadingHTTPServer(
        ("0.0.0.0", PORT), UniversalAppHandler
    )
    print(f"Fit Studio 2026 Universal Server started on port {PORT}")
    server.serve_forever()
