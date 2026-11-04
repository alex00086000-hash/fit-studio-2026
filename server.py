import base64
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import socket
import sqlite3
import sys


def get_app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


CLOUD_DB = os.path.join(get_app_dir(), "cloud_tracker.db")
PORT = int(os.environ.get("PORT", 8000))

# НАСТРОЙКА ДОСТУПА ПО ПАРОЛЮ
# Вы можете изменить логин и пароль на свои собственные:
AUTH_USER = "admin"
AUTH_PASS = "123321222"


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


WEB_APP_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Fit Studio 2026 Online</title>
<style>
  :root { --lime: #C6F16D; --card: #14230D; --input: #223816; --accent: #9AE61C; --text: #F8FAFC; --muted: #8DAA7C; --warn: #F43F5E; }
  * { box-sizing: border-box; font-family: 'Segoe UI', system-ui, sans-serif; }
  body { margin: 0; padding: 16px; background: var(--lime); color: var(--card); }
  .header { display: flex; justify-content: space-between; align-items: center; max-width: 900px; margin: 0 auto 14px; flex-wrap: wrap; gap: 8px; }
  .header h1 { margin: 0; font-size: 22px; font-weight: 800; }
  .badge { background: var(--card); color: var(--accent); padding: 6px 12px; border-radius: 20px; font-weight: 700; font-size: 12px; }
  .bento { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px; max-width: 900px; margin: 0 auto; }
  .puzzle { background: var(--card); color: var(--text); padding: 18px; border-radius: 16px; }
  .puzzle h3 { margin: 0 0 6px; color: var(--accent); font-size: 13px; }
  .big-val { font-size: 24px; font-weight: 800; margin: 4px 0; }
  .sub { color: var(--muted); font-size: 12px; margin-bottom: 10px; }
  input { width: 100%; padding: 9px; margin: 4px 0; border-radius: 8px; border: none; background: var(--input); color: var(--text); font-size: 14px; }
  button { width: 100%; padding: 10px; margin-top: 6px; border-radius: 8px; border: none; background: var(--accent); color: var(--card); font-weight: 800; cursor: pointer; }
</style>
</head>
<body>
  <div class="header">
    <h1>🧩 FIT STUDIO 2026 WEB</h1>
    <span class="badge">🔒 Защищено паролем</span>
  </div>
  <div class="bento">
    <div class="puzzle">
      <h3>📊 ВЕС И ШАГИ</h3>
      <div class="big-val" id="currWeight">-- кг</div>
      <div class="sub" id="weightSub">Загрузка...</div>
      <input type="number" step="0.1" id="wInput" placeholder="Вес утром (кг)">
      <input type="number" id="sInput" placeholder="Пройдено шагов">
      <button onclick="sendEntry()">Сохранить вес</button>
    </div>
    <div class="puzzle">
      <h3>🍲 ПРИЕМ ПИЩИ</h3>
      <div class="big-val" id="calVal">0 ккал</div>
      <div class="sub" id="mealSub">Приемов за сегодня: 0</div>
      <input type="text" id="fName" placeholder="Продукт / блюдо">
      <input type="number" id="fGrams" placeholder="Граммы" value="150">
      <input type="number" id="fCal" placeholder="Калории порции (ккал)">
      <button onclick="sendMeal()">+ Добавить еду</button>
    </div>
    <div class="puzzle">
      <h3>🏋️ ТРЕНИРОВКА</h3>
      <div class="big-val" id="burnVal" style="color:var(--warn)">🔥 0 ккал</div>
      <div class="sub" id="workSub">Время: 0 мин</div>
      <input type="text" id="wkName" placeholder="Активность / Прогулка">
      <input type="number" id="wkMin" placeholder="Минуты" value="30">
      <input type="number" id="wkCal" placeholder="Сожжено (ккал)" value="140">
      <button onclick="sendWork()">+ Записать активность</button>
    </div>
  </div>
<script>
  async function refresh() {
    const r = await fetch('/api/state');
    const d = await r.json();
    document.getElementById('currWeight').innerText = d.curr_weight;
    document.getElementById('weightSub').innerText = d.weight_sub;
    document.getElementById('calVal').innerText = d.today_cal + ' ккал';
    document.getElementById('mealSub').innerText = 'Приемов за сегодня: ' + d.meal_count;
    document.getElementById('burnVal').innerText = '🔥 ' + d.today_burn + ' ккал';
    document.getElementById('workSub').innerText = 'Время активности: ' + d.today_min + ' мин';
  }
  async function sendEntry() {
    const w = parseFloat(document.getElementById('wInput').value || 0);
    const s = parseInt(document.getElementById('sInput').value || 0);
    await fetch('/api/add_entry', {method:'POST', body:JSON.stringify({weight:w, steps:s})});
    document.getElementById('wInput').value = '';
    document.getElementById('sInput').value = '';
    refresh();
  }
  async function sendMeal() {
    const name = document.getElementById('fName').value.trim();
    const grams = parseFloat(document.getElementById('fGrams').value || 100);
    const cal = parseFloat(document.getElementById('fCal').value || 0);
    if (!name) return;
    await fetch('/api/add_meal', {method:'POST', body:JSON.stringify({name, grams, cal})});
    document.getElementById('fName'].value = '';
    document.getElementById('fCal').value = '';
    refresh();
  }
  async function sendWork() {
    const name = document.getElementById('wkName').value.trim();
    const mins = parseFloat(document.getElementById('wkMin').value || 30);
    const cal = parseFloat(document.getElementById('wkCal').value || 100);
    if (!name) return;
    await fetch('/api/add_workout', {method:'POST', body:JSON.stringify({name, mins, cal})});
    document.getElementById('wkName').value = '';
    refresh();
  }
  refresh();
</script>
</body>
</html>
"""


class CloudRequestHandler(BaseHTTPRequestHandler):

    def check_auth(self):
        auth_header = self.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Basic "):
            return False
        try:
            encoded = auth_header.split(" ")[1]
            decoded = (
                base64.b64decode(encoded).decode("utf-8").split(":", 1)
            )
            username, password = decoded[0], decoded[1]
            return username == AUTH_USER and password == AUTH_PASS
        except Exception:
            return False

    def request_auth(self):
        self.send_response(401)
        self.send_header(
            "WWW-Authenticate", 'Basic realm="Fit Studio Protected Area"'
        )
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Authorization required")

    def _send_json(self, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.check_auth():
            self.request_auth()
            return

        if self.path == "/api/state":
            with sqlite3.connect(CLOUD_DB) as conn:
                cur = conn.cursor()
                entries = [
                    e for e in get_full_state()["entries"] if e["weight"] > 0
                ]
                curr_w = (
                    f"{entries[-1]['weight']:.1f} кг" if entries else "-- кг"
                )
                target = get_full_state()["settings"].get(
                    "target_weight", "--"
                )
                today = datetime.now().strftime("%Y-%m-%d")
                meals = [
                    m for m in get_full_state()["meals"] if m["date"] == today
                ]
                workouts = [
                    w
                    for w in get_full_state()["workouts"]
                    if w["date"] == today
                ]
                self._send_json(
                    {
                        "curr_weight": curr_w,
                        "weight_sub": f"Цель: {target} кг",
                        "today_cal": int(sum(m["calories"] for m in meals)),
                        "meal_count": len(meals),
                        "today_burn": int(
                            sum(w["calories_burned"] for w in workouts)
                        ),
                        "today_min": int(
                            sum(w["duration_min"] for w in workouts)
                        ),
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
        if not self.check_auth():
            self.request_auth()
            return

        length = int(self.headers.get("Content-Length", 0))
        data = json.loads(self.rfile.read(length).decode("utf-8"))
        today = datetime.now().strftime("%Y-%m-%d")

        with sqlite3.connect(CLOUD_DB) as conn:
            cur = conn.cursor()
            if self.path == "/api/add_entry":
                cur.execute(
                    """INSERT INTO entries (date, weight, calories, steps, note)
                       VALUES (?, ?, 0, ?, 'Web')
                       ON CONFLICT(date) DO UPDATE SET weight = excluded.weight, steps = excluded.steps""",
                    (
                        today,
                        float(data.get("weight", 0)),
                        int(data.get("steps", 0)),
                    ),
                )
            elif self.path == "/api/add_meal":
                cur.execute(
                    """INSERT INTO meal_log (date, food_name, grams, calories, protein, fat, carbs)
                       VALUES (?, ?, ?, ?, 0, 0, 0)""",
                    (
                        today,
                        data["name"],
                        float(data["grams"]),
                        float(data["cal"]),
                    ),
                )
            elif self.path == "/api/add_workout":
                cur.execute(
                    """INSERT INTO workout_log (date, workout_name, duration_min, calories_burned, note)
                       VALUES (?, ?, ?, ?, 'Web')""",
                    (
                        today,
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
    init_cloud_db()
    print("=" * 60)
    print(f"СЕРВЕР ЗАПУЩЕН НА ПОРТУ {PORT} С ЗАЩИТОЙ ПАРОЛЕМ")
    print("=" * 60)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), CloudRequestHandler)
    server.serve_forever()
