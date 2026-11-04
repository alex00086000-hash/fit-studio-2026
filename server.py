import json
import os
import socket
import sqlite3
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def get_app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


CLOUD_DB = os.path.join(get_app_dir(), "cloud_tracker.db")
PORT = int(os.environ.get("PORT", 8000))


def init_cloud_db():
    with sqlite3.connect(CLOUD_DB) as conn:
        cur = conn.cursor()
        cur.execute(
            "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)"
        )
        cur.execute(
            """CREATE TABLE IF NOT EXISTS entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT UNIQUE,
                weight REAL,
                calories INTEGER,
                steps INTEGER,
                note TEXT
            )"""
        )
        cur.execute(
            """CREATE TABLE IF NOT EXISTS meal_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                food_name TEXT,
                grams REAL,
                calories REAL,
                protein REAL DEFAULT 0,
                fat REAL DEFAULT 0,
                carbs REAL DEFAULT 0,
                UNIQUE(date, food_name, grams, calories)
            )"""
        )
        cur.execute(
            """CREATE TABLE IF NOT EXISTS workout_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                workout_name TEXT,
                duration_min REAL,
                calories_burned REAL,
                note TEXT,
                UNIQUE(date, workout_name, duration_min, calories_burned)
            )"""
        )
        conn.commit()


def get_full_state():
    with sqlite3.connect(CLOUD_DB) as conn:
        cur = conn.cursor()
        cur.execute("SELECT key, value FROM settings")
        settings = dict(cur.fetchall())

        cur.execute(
            "SELECT date, weight, calories, steps, note FROM entries ORDER BY date ASC"
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
            "SELECT date, food_name, grams, calories, protein, fat, carbs FROM meal_log ORDER BY id DESC"
        )
        meals = [
            {
                "date": r[0],
                "food_name": r[1],
                "grams": r[2],
                "calories": r[3],
                "protein": r[4],
                "fat": r[5],
                "carbs": r[6],
            }
            for r in cur.fetchall()
        ]

        cur.execute(
            "SELECT date, workout_name, duration_min, calories_burned, note FROM workout_log ORDER BY id DESC"
        )
        workouts = [
            {
                "date": r[0],
                "workout_name": r[1],
                "duration_min": r[2],
                "calories_burned": r[3],
                "note": r[4],
            }
            for r in cur.fetchall()
        ]

        return {
            "settings": settings,
            "entries": entries,
            "meals": meals,
            "workouts": workouts,
        }


def sync_data(payload):
    with sqlite3.connect(CLOUD_DB) as conn:
        cur = conn.cursor()

        for k, v in payload.get("settings", {}).items():
            cur.execute(
                """INSERT INTO settings (key, value) VALUES (?, ?)
                   ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
                (k, str(v)),
            )

        for e in payload.get("entries", []):
            cur.execute(
                """INSERT INTO entries (date, weight, calories, steps, note)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(date) DO UPDATE SET
                       weight = CASE WHEN excluded.weight > 0 THEN excluded.weight ELSE entries.weight END,
                       calories = CASE WHEN excluded.calories > 0 THEN excluded.calories ELSE entries.calories END,
                       steps = CASE WHEN excluded.steps > 0 THEN excluded.steps ELSE entries.steps END,
                       note = CASE WHEN excluded.note != '' THEN excluded.note ELSE entries.note END""",
                (
                    e["date"],
                    float(e.get("weight", 0)),
                    int(e.get("calories", 0)),
                    int(e.get("steps", 0)),
                    e.get("note", ""),
                ),
            )

        for m in payload.get("meals", []):
            cur.execute(
                """INSERT OR IGNORE INTO meal_log (date, food_name, grams, calories, protein, fat, carbs)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    m["date"],
                    m["food_name"],
                    float(m["grams"]),
                    float(m["calories"]),
                    float(m.get("protein", 0)),
                    float(m.get("fat", 0)),
                    float(m.get("carbs", 0)),
                ),
            )

        for w in payload.get("workouts", []):
            cur.execute(
                """INSERT OR IGNORE INTO workout_log (date, workout_name, duration_min, calories_burned, note)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    w["date"],
                    w["workout_name"],
                    float(w["duration_min"]),
                    float(w["calories_burned"]),
                    w.get("note", ""),
                ),
            )

        conn.commit()
    return get_full_state()


# Встроенный мобильный и десктопный Web-интерфейс (Салатовый Пазл)
WEB_APP_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Fit Studio 2026 Online</title>
<style>
  :root {
    --lime: #C6F16D;
    --card: #14230D;
    --input: #223816;
    --accent: #9AE61C;
    --text: #F8FAFC;
    --muted: #8DAA7C;
    --warn: #F43F5E;
  }
  * { box-sizing: border-box; font-family: 'Segoe UI', system-ui, sans-serif; }
  body { margin: 0; padding: 20px; background: var(--lime); color: var(--card); }
  .header { display: flex; justify-content: space-between; align-items: center; max-width: 960px; margin: 0 auto 16px; flex-wrap: wrap; gap: 10px; }
  .header h1 { margin: 0; font-size: 24px; font-weight: 800; }
  .badge { background: var(--card); color: var(--accent); padding: 6px 14px; border-radius: 20px; font-weight: 700; font-size: 13px; }
  .bento { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 14px; max-width: 960px; margin: 0 auto; }
  .puzzle { background: var(--card); color: var(--text); padding: 20px; border-radius: 16px; display: flex; flex-direction: column; justify-content: space-between; }
  .puzzle h3 { margin: 0 0 8px; color: var(--accent); font-size: 14px; letter-spacing: 0.5px; }
  .big-val { font-size: 26px; font-weight: 800; margin: 6px 0; }
  .sub { color: var(--muted); font-size: 13px; margin-bottom: 12px; }
  input, select { width: 100%; padding: 10px; margin: 5px 0; border-radius: 8px; border: none; background: var(--input); color: var(--text); font-size: 14px; }
  button { width: 100%; padding: 11px; margin-top: 8px; border-radius: 8px; border: none; background: var(--accent); color: var(--card); font-weight: 800; font-size: 14px; cursor: pointer; }
  button:hover { opacity: 0.9; }
  table { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 13px; }
  th, td { text-align: left; padding: 8px; border-bottom: 1px solid var(--input); }
  th { color: var(--accent); }
</style>
</head>
<body>
  <div class="header">
    <h1>🧩 FIT STUDIO 2026 ONLINE</h1>
    <span class="badge" id="statusBadge">🟢 Облако активно</span>
  </div>

  <div class="bento">
    <!-- Пазл 1: Сводка веса -->
    <div class="puzzle">
      <div>
        <h3>📊 ДИНАМИКА ВЕСА</h3>
        <div class="big-val" id="currWeight">-- кг</div>
        <div class="sub" id="weightDiff">Синхронизация с базой...</div>
      </div>
      <div>
        <input type="date" id="entryDate">
        <input type="number" step="0.1" id="entryWeight" placeholder="Вес утром (кг)">
        <input type="number" id="entrySteps" placeholder="Шаги за день">
        <button onclick="submitEntry()">Сохранить вес в облако</button>
      </div>
    </div>

    <!-- Пазл 2: Питание -->
    <div class="puzzle">
      <div>
        <h3>🍲 ДНЕВНИК ПИТАНИЯ</h3>
        <div class="big-val" id="todayCal">0 ккал</div>
        <div class="sub" id="todayMacros">Б: 0г | Ж: 0г | У: 0г</div>
      </div>
      <div>
        <input type="text" id="foodName" placeholder="Блюдо (напр. Куриное филе)">
        <input type="number" id="foodGrams" placeholder="Вес порции (граммы)" value="150">
        <input type="number" id="foodCal" placeholder="Калории порции (ккал)">
        <button onclick="submitMeal()">+ Добавить прием пищи</button>
      </div>
    </div>

    <!-- Пазл 3: Тренировки -->
    <div class="puzzle">
      <div>
        <h3>🏋️ ТРЕНИРОВКИ & АКТИВНОСТЬ</h3>
        <div class="big-val" id="todayBurn" style="color: var(--warn);">🔥 0 ккал</div>
        <div class="sub" id="todayTime">Активность за сегодня: 0 мин</div>
      </div>
      <div>
        <input type="text" id="workName" placeholder="Упражнение / Прогулка">
        <input type="number" id="workMin" placeholder="Время (минут)" value="30">
        <input type="number" id="workCal" placeholder="Сожжено (ккал)" value="140">
        <button onclick="submitWorkout()">+ Записать тренировку</button>
      </div>
    </div>

    <!-- Пазл 4: История записей -->
    <div class="puzzle" style="grid-column: 1 / -1;">
      <h3>📋 ПОСЛЕДНИЕ ЗАПИСИ В ОБЛАКЕ (СИНХРОНИЗИРОВАНО С ПК)</h3>
      <table id="historyTable">
        <thead><tr><th>Дата</th><th>Вес (кг)</th><th>Ккал</th><th>Шаги</th></tr></thead>
        <tbody></tbody>
      </table>
    </div>
  </div>

<script>
  const todayStr = new Date().toISOString().split('T')[0];
  document.getElementById('entryDate').value = todayStr;

  async function loadState() {
    const res = await fetch('/api/state');
    const data = await res.json();

    const valid = data.entries.filter(e => e.weight > 0);
    if (valid.length > 0) {
      const curr = valid[valid.length - 1].weight;
      const start = valid[0].weight;
      const diff = (curr - start).toFixed(1);
      const sign = diff > 0 ? '+' : '';
      document.getElementById('currWeight').innerText = curr.toFixed(1) + ' кг';
      document.getElementById('weightDiff').innerText = `Изменение со старта: ${sign}${diff} кг | Цель: ${data.settings.target_weight || '--'} кг`;
    }

    const todayMeals = data.meals.filter(m => m.date === todayStr);
    const calSum = todayMeals.reduce((acc, m) => acc + m.calories, 0);
    const pSum = todayMeals.reduce((acc, m) => acc + (m.protein || 0), 0);
    const fSum = todayMeals.reduce((acc, m) => acc + (m.fat || 0), 0);
    const cSum = todayMeals.reduce((acc, m) => acc + (m.carbs || 0), 0);
    document.getElementById('todayCal').innerText = Math.round(calSum) + ' ккал';
    document.getElementById('todayMacros').innerText = `За сегодня (${todayMeals.length} приемов) | Б:${pSum.toFixed(0)}г Ж:${fSum.toFixed(0)}г У:${cSum.toFixed(0)}г`;

    const todayWork = data.workouts.filter(w => w.date === todayStr);
    const burnSum = todayWork.reduce((acc, w) => acc + w.calories_burned, 0);
    const minSum = todayWork.reduce((acc, w) => acc + w.duration_min, 0);
    document.getElementById('todayBurn').innerText = '🔥 ' + Math.round(burnSum) + ' ккал';
    document.getElementById('todayTime').innerText = `Активность за сегодня: ${Math.round(minSum)} мин`;

    const tbody = document.querySelector('#historyTable tbody');
    tbody.innerHTML = '';
    data.entries.slice().reverse().slice(0, 8).forEach(e => {
      tbody.innerHTML += `<tr><td>${e.date}</td><td><b>${e.weight} кг</b></td><td>${e.calories}</td><td>${e.steps}</td></tr>`;
    });
  }

  async function submitEntry() {
    const date = document.getElementById('entryDate').value || todayStr;
    const weight = parseFloat(document.getElementById('entryWeight').value || 0);
    const steps = parseInt(document.getElementById('entrySteps').value || 0);
    await fetch('/api/sync', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({entries: [{date, weight, calories: 0, steps, note: 'Из Web/Телефона'}]})
    });
    document.getElementById('entryWeight').value = '';
    document.getElementById('entrySteps').value = '';
    loadState();
  }

  async function submitMeal() {
    const food_name = document.getElementById('foodName').value.trim();
    const grams = parseFloat(document.getElementById('foodGrams').value || 100);
    const calories = parseFloat(document.getElementById('foodCal').value || 0);
    if (!food_name) return;
    await fetch('/api/sync', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({meals: [{date: todayStr, food_name, grams, calories, protein: 0, fat: 0, carbs: 0}]})
    });
    document.getElementById('foodName').value = '';
    document.getElementById('foodCal').value = '';
    loadState();
  }

  async function submitWorkout() {
    const workout_name = document.getElementById('workName').value.trim();
    const duration_min = parseFloat(document.getElementById('workMin').value || 30);
    const calories_burned = parseFloat(document.getElementById('workCal').value || 100);
    if (!workout_name) return;
    await fetch('/api/sync', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({workouts: [{date: todayStr, workout_name, duration_min, calories_burned, note: 'Web'}]})
    });
    document.getElementById('workName').value = '';
    loadState();
  }

  loadState();
</script>
</body>
</html>
"""


class CloudRequestHandler(BaseHTTPRequestHandler):

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/state":
            self._send_json(get_full_state())
        else:
            body = WEB_APP_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def do_POST(self):
        if self.path == "/api/sync":
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length).decode("utf-8")
            payload = json.loads(raw) if raw else {}
            updated_state = sync_data(payload)
            self._send_json(updated_state)
        else:
            self._send_json({"error": "Not found"}, 404)

    def log_message(self, format, *args):
        # Тихий лог в консоли без лишнего шума
        pass


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


if __name__ == "__main__":
    init_cloud_db()
    local_ip = get_local_ip()
    print("=" * 60)
    print("ОНЛАЙН-СЕРВЕР FIT STUDIO 2026 ЗАПУЩЕН!")
    print(f"1. Открыть в браузере на ПК:     http://127.0.0.1:{PORT}")
    print(f"2. Открыть с телефона (Wi-Fi):   http://{local_ip}:{PORT}")
    print(f"3. Адрес API для ПК-клиента:     http://127.0.0.1:{PORT}")
    print("=" * 60)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), CloudRequestHandler)
    server.serve_forever()
