import base64
import datetime
import hashlib
import hmac
import http.server
import json
import os
import sqlite3
import urllib.parse

PORT = int(os.environ.get("PORT", 8000))
TELEGRAM_BOT_TOKEN = "8691354583:AAGGk0G8r1g-0Jbe-L_FZyN8X0bsYtt01yQ"  # Замените на токен от @BotFather


def verify_telegram_init_data(init_data_str, bot_token):
    """Проверка подлинности данных, переданных из Telegram Mini App"""
    try:
        parsed = urllib.parse.parse_qsl(init_data_str, keep_blank_values=True)
        data = dict(parsed)
        received_hash = data.pop("hash", None)
        if not received_hash:
            return False

        # Сортируем параметры для сверки хэша по стандарту Telegram
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


# HTML-версия с поддержкой Telegram WebApp SDK
WEB_APP_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<title>Fit Studio 2026 Telegram</title>
<style>
  :root { --lime: #C6F16D; --card: #14230D; --input: #223816; --accent: #9AE61C; --text: #F8FAFC; --muted: #8DAA7C; --warn: #F43F5E; }
  * { box-sizing: border-box; font-family: 'Segoe UI', system-ui, sans-serif; }
  body { margin: 0; padding: 16px; background: var(--lime); color: var(--card); }
  .header { display: flex; justify-content: space-between; align-items: center; max-width: 900px; margin: 0 auto 14px; }
  .header h1 { margin: 0; font-size: 20px; font-weight: 800; }
  .badge { background: var(--card); color: var(--accent); padding: 5px 12px; border-radius: 20px; font-weight: 700; font-size: 11px; }
  .bento { display: grid; grid-template-columns: 1fr; gap: 12px; max-width: 900px; margin: 0 auto; }
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
    <span class="badge" id="userBadge">👤 Авторизован в Telegram</span>
  </div>
  <div class="bento">
    <div class="puzzle">
      <h3>📊 УТРЕННИЙ ВЕС И ШАГИ</h3>
      <div class="big-val" id="currWeight">-- кг</div>
      <div class="sub" id="weightSub">Загрузка данных...</div>
      <input type="number" step="0.1" id="wInput" placeholder="Вес утром (кг)">
      <input type="number" id="sInput" placeholder="Пройдено шагов">
      <button onclick="sendEntry()">Сохранить в облако</button>
    </div>
  </div>
<script>
  // Инициализация Telegram WebApp
  const tg = window.Telegram.WebApp;
  tg.expand(); // Разворачиваем на весь экран в Telegram

  const initData = tg.initData || "";

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
    if (tg.initDataUnsafe && tg.initDataUnsafe.user) {
      document.getElementById('userBadge').innerText = "👤 " + (tg.initDataUnsafe.user.first_name || "Пользователь");
    }
  }

  async function sendEntry() {
    const weight = parseFloat(document.getElementById('wInput').value || 0);
    const steps = parseInt(document.getElementById('sInput').value || 0);
    await apiFetch('/api/add_entry', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({weight, steps})
    });
    document.getElementById('wInput').value = '';
    document.getElementById('sInput').value = '';
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
            # Проверяем подлинность от Telegram
            if not verify_telegram_init_data(init_data, TELEGRAM_BOT_TOKEN):
                self.send_response(403)
                self.end_headers()
                self.wfile.write(
                    json.dumps(
                        {"error": "Unauthorized Telegram session"}
                    ).encode("utf-8")
                    )
                return

            # Отдаем демо-данные
            body = json.dumps(
                {
                    "curr_weight": "84.5 кг",
                    "weight_sub": "Цель: 73.0 кг | Синхронизировано",
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

        length = int(self.headers.get("Content-Length", 0))
        data = json.loads(self.rfile.read(length).decode("utf-8"))
        # Здесь сохранение данных в базу данных SQLite для конкретного пользователя Telegram

        body = json.dumps({"ok": True}, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    server = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), TelegramAppHandler)
    print(f"Telegram WebApp Server started on port {PORT}")
    server.serve_forever()
