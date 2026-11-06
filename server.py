import base64
from datetime import datetime
import hashlib
import hmac
import http.server
import json
import os
import sqlite3
import urllib.parse

PORT = int(os.environ.get("PORT", 8000))
TELEGRAM_BOT_TOKEN = "ВАШ_ТОКЕН_БОТА_ИЗ_BOTFATHER"  # Ваш токен от @BotFather

DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "cloud_tracker.db"
)


def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(
            "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)"
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
                calories REAL
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


def verify_telegram_init_data(init_data_str, bot_token):
    try:
        parsed = urllib.parse.parse_qsl(init_data_str, keep_blank_values=True)
        data = dict(parsed)
        received_hash = data.pop("hash", None)
        if not received_hash:
            return False

        data_check_string = "\n".join(
            f"{k}={v}" for k, v in sorted(data.items())
        )
        secret_key = hmac.new(
            b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256
        ).digest()
        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(calculated_hash, received_hash)
    except Exception:
        return False


def get_user_id(init_data_str):
    try:
        parsed = urllib.parse.parse_qsl(init_data_str, keep_blank_values=True)
        data = dict(parsed)
        user_json = json.loads(data.get("user", "{}"))
        return str(user_json.get("id", "default_user"))
    except Exception:
        return "default_user"


# Полноценный веб-интерфейс в Telegram в салатовом стиле пазлов
WEB_APP_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<title>Fit Studio 2026 Online</title>
<style>
  :root { --lime: #C6F16D; --card: #14230D; --input: #223816; --accent: #9AE61C; --text: #F8FAFC; --muted: #8DAA7C; --warn: #F43F5E; }
  * { box-sizing: border-box; font-family: 'Segoe UI', system-ui, sans-serif; }
  body { margin: 0; padding: 16px; background: var(--lime); color: var(--card); }
  .header { display: flex; justify-content: space-between; align-items: center; max-width: 900px; margin: 0 auto 14px; flex-wrap: wrap; gap: 8px; }
  .header h1 { margin: 0; font-size: 20px; font-weight: 800; }
  .badge { background: var(--card); color: var(--accent); padding: 5px 12px; border-radius: 20px; font-weight: 700; font-size: 11px; }
  .bento { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px; max-width: 900px; margin: 0 auto; }
  .puzzle { background: var(--card); color: var(--text); padding: 18px; border-radius: 16px; }
  .puzzle h3 { margin: 0 0 6px; color: var(--accent); font-size: 13px; }
  .big-val { font-size: 24px; font-weight: 800; margin: 4px 0; }
  .sub { color: var(--muted); font-size: 12px; margin-bottom: 10px; }
  input { width: 100%; padding: 10px; margin: 4px 0; border-radius: 8px; border: none; background: var(--input); color: var(--text); font-size: 14px; }
  button { width: 100%; padding: 12px; margin-top: 6px; border-radius: 8px; border: none; background: var(--accent); color: var(--card); font-weight: 800; cursor: pointer; }
</style>
</head>
<body>
  <div class="header">
    <h1>🧩 FIT STUDIO TG</h1>
    <span class="badge" id="userBadge">👤 Загрузка...</span>
  </div>
  <div class="bento">
    <div class="puzzle">
      <h3>📊 ДИНАМИКА ВЕСА И ШАГИ</h3>
      <div class="big-val" id="currWeight">-- кг</div>
      <div class="sub" id="weightSub">Синхронизация...</div>
      <input type="date" id="entryDate">
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
      <input type="number" id="fCal" placeholder="Калории (ккал)">
      <button onclick="sendMeal()">+ Добавить еду</button>
    </div>
    <div class="puzzle">
      <h3>🏋️ ТРЕНИРОВКА</h3>
      <div class="big-val" id="burnVal" style="color:var(--warn)">🔥 0 ккал</div>
      <div class="sub" id="workSub">Активность за сегодня: 0 мин</div>
      <input type="text" id="wkName" placeholder="Упражнение / Прогулка">
      <input type="number" id="wkMin" placeholder="Минуты" value="30">
      <input type="number" id="wkCal" placeholder="Сожжено (ккал)" value="140">
      <button onclick="sendWork()">+ Записать тренировку</button>
    </div>
  </div>
<script>
  const tg = window.Telegram.WebApp;
  tg.expand();
  const initData = tg.initData || "";
  
  const todayStr = new Date().toISOString().split('T')[0];
  document.getElementById('entryDate').value = todayStr;

  if (tg.initDataUnsafe && tg.initDataUnsafe.user) {
    document.getElementById('userBadge').innerText = "👤 " + (tg.initDataUnsafe.user.first_name || "Пользователь");
  }

  async function apiFetch(url, options = {}) {
    options.headers = options.headers || {};
    options.headers['X-Telegram-Init-Data'] = initData;
    const res = await fetch(url, options);
    return res.json();
  }

  async function refresh() {
    const d = await apiFetch('/api/state');
    if (d.error) {
      document.body.innerHTML = "<h2 style='padding:20px; text-align:center;'>Ошибка доступа: откройте приложение из Telegram!</h2>";
      return;
    }
    document.getElementById('currWeight').innerText = d.curr_weight;
    document.getElementById('weightSub').innerText = d.weight_sub;
    document.getElementById('calVal').innerText = d.today_cal + ' ккал';
    document.getElementById('mealSub').innerText = 'Приемов за сегодня: ' + d.meal_count;
    document.getElementById('burnVal').innerText = '🔥 ' + d.today_burn + ' ккал';
    document.getElementById('workSub').innerText = 'Активность: ' + d.today_min + ' мин';
  }

  async function sendEntry() {
    const date = document.getElementById('entryDate').value || todayStr;
    const weight = parseFloat(document.getElementById('wInput').value || 0);
    const steps = parseInt(document.getElementById('sInput').value || 0);
    await apiFetch('/api/add_entry', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({date, weight, steps})
    });
    document.getElementById('wInput').value = '';
    document.getElementById('sInput').value = '';
    refresh();
  }

  async function sendMeal() {
    const name = document.getElementById('fName').value.trim();
    const grams = parseFloat(document.getElementById('fGrams').value || 100);
    const cal = parseFloat(document.getElementById('fCal').value || 0);
    if (!name) return;
    await apiFetch('/api/add_meal', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({date: todayStr, name, grams, cal})
    });
    document.getElementById('fName').value = '';
    document.getElementById('fCal').value = '';
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
    refresh();
  }

  refresh();
</script>
</body>
</html>
"""


class TelegramAppHandler(http.server.BaseHTTPRequestHandler):

    def do_GET(self):
        if self.path == "/api/state":
            init_data = self.headers.get("X-Telegram-Init-Data", "")
            if not verify_telegram_init_data(init_data, TELEGRAM_BOT_TOKEN):
                self.send_response(403)
                self.end_headers()
                self.wfile.write(
                    json.dumps(
                        {"error": "Unauthorized Telegram session"}
                    ).encode("utf-8")
                )
                return

            user_id = get_user_id(init_data)
            today = datetime.datetime.now().strftime("%Y-%m-%d")

            with sqlite3.connect(DB_PATH) as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT date, weight, steps FROM entries WHERE telegram_id = ? ORDER BY date ASC",
                    (user_id,),
                )
                entries = cur.fetchall()

                cur.execute(
                    "SELECT calories FROM meal_log WHERE telegram_id = ? AND date = ?",
                    (user_id, today),
                )
                meals = cur.fetchall()

                cur.execute(
                    "SELECT duration_min, calories_burned FROM workout_log WHERE telegram_id = ? AND date = ?",
                    (user_id, today),
                )
                workouts = cur.fetchall()

            valid_entries = [e for e in entries if e[1] > 0]
            curr_w = (
                f"{valid_entries[-1][1]:.1f} кг"
                if valid_entries
                else "-- кг"
            )
            sub = (
                f"Всего записей: {len(entries)} | Шаги за сегодня"
                if entries
                else "Добавьте первую отметку веса"
            )

            today_cal = sum(m[0] for m in meals)
            today_burn = sum(w[1] for w in workouts)
            today_min = sum(w[0] for w in workouts)

            body = json.dumps(
                {
                    "curr_weight": curr_w,
                    "weight_sub": sub,
                    "today_cal": int(today_cal),
                    "meal_count": len(meals),
                    "today_burn": int(today_burn),
                    "today_min": int(today_min),
                },
                ensure_ascii=False,
            ).encode("utf-8")

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)
        else:
            body = WEB_APP_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def do_POST(self):
        init_data = self.headers.get("X-Telegram-Init-Data", "")
        if not verify_telegram_init_data(init_data, TELEGRAM_BOT_TOKEN):
            self.send_response(403)
            self.end_headers()
            return

        user_id = get_user_id(init_data)
        length = int(self.headers.get("Content-Length", 0))
        data = json.loads(self.rfile.read(length).decode("utf-8"))

        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            if self.path == "/api/add_entry":
                date = data.get("date")
                weight = float(data.get("weight", 0))
                steps = int(data.get("steps", 0))
                cur.execute(
                    """INSERT INTO entries (telegram_id, date, weight, calories, steps, note)
                       VALUES (?, ?, ?, 0, ?, 'Telegram WebApp')
                       ON CONFLICT(telegram_id, date) DO UPDATE SET
                           weight = CASE WHEN excluded.weight > 0 THEN excluded.weight ELSE entries.weight END,
                           steps = CASE WHEN excluded.steps > 0 THEN excluded.steps ELSE entries.steps END""",
                    (user_id, date, weight, steps),
                )
            elif self.path == "/api/add_meal":
                cur.execute(
                    """INSERT INTO meal_log (telegram_id, date, food_name, grams, calories)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        user_id,
                        data["date"],
                        data["name"],
                        float(data["grams"]),
                        float(data["cal"]),
                    ),
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

        body = json.dumps({"ok": True}, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    init_db()
    server = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), TelegramAppHandler)
    print(f"Telegram Full App Server started on port {PORT}")
    server.serve_forever()
