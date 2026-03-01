# Pro.Альбом — License API Server

Промежуточный сервер лицензий между клиентами и Google Sheets.

## Архитектура

```
Клиент (VignetteCore)
    ↓ HTTPS POST (key + hwid)
Render.com API Server (FastAPI)
    ↓ gspread
Google Sheets (VignetteCloud_Licenses)
```

**Зачем:** credentials.json больше не нужен на каждом клиенте.  
Безопасность, централизация, контроль.

---

## Быстрый старт (деплой на Render.com)

### 1. Подготовка GitHub-репозитория

```bash
# Создайте новый репозиторий на GitHub: proalbom-license-api
cd H:\VignetteCloud\license-api
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/YOUR_USERNAME/proalbom-license-api.git
git branch -M main
git push -u origin main
```

### 2. Регистрация на Render.com

1. Перейдите на [render.com](https://render.com) → **Sign Up**
2. Зарегистрируйтесь через **GitHub** (автоматически свяжет аккаунты)
3. Подтвердите email

### 3. Создание Web Service

1. Dashboard → **New** → **Web Service**
2. Подключите репозиторий `proalbom-license-api`
3. Настройки:
   - **Name:** `proalbom-license-api`
   - **Region:** `Frankfurt (EU)` ← ближайший к России
   - **Branch:** `main`
   - **Runtime:** `Python`
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `uvicorn api:app --host 0.0.0.0 --port $PORT`
   - **Plan:** `Free` (достаточно для 50–100 запросов/день)

### 4. Переменные окружения (Environment Variables)

В разделе **Environment** → **Add Environment Variable**:

| Key | Value |
|-----|-------|
| `API_SECRET` | Сгенерируйте: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `GOOGLE_CREDENTIALS_JSON` | Содержимое файла `credentials.json` целиком (весь JSON) |
| `SPREADSHEET_NAME` | `VignetteCloud_Licenses` |
| `PYTHON_VERSION` | `3.12` |

> ⚠️ **GOOGLE_CREDENTIALS_JSON**: откройте `credentials.json` в текстовом редакторе,  
> скопируйте **весь текст** и вставьте как значение переменной.

### 5. Деплой

Нажмите **Deploy Web Service**. Render:
- Установит Python 3.12
- Выполнит `pip install -r requirements.txt`
- Запустит `uvicorn api:app`

Через 1–2 минуты сервис будет доступен по адресу:
```
https://proalbom-license-api.onrender.com
```

### 6. Проверка

```bash
curl https://proalbom-license-api.onrender.com/api/health
# → {"status":"ok","date":"2025-06-01"}
```

---

## API Endpoints

### GET /api/health
Проверка работоспособности.
```json
{"status": "ok", "date": "2025-06-01"}
```

### POST /api/verify
Проверка лицензии.
```json
// Request
{"key": "XXXXX-XXXXX", "hwid": "abc123..."}
// Headers: Authorization: Bearer <API_SECRET>

// Response (ok)
{"valid": true, "plan": "pro", "plan_label": "Pro", "status": "Active",
 "expiration": "2026-01-01", "days_left": 214, "server_date": "2025-06-01"}

// Response (error)
{"valid": false, "error_code": "hwid_mismatch",
 "error": "Лицензия привязана к другому компьютеру."}
```

### POST /api/activate
Активация ключа.
```json
// Request
{"key": "XXXXX-XXXXX", "hwid": "abc123...", "pc_name": "DESKTOP-123", "email": "user@mail.ru"}

// Response (ok)
{"success": true, "plan": "pro", "plan_label": "Pro",
 "expiration": "2026-01-01", "days_left": 365,
 "message": "Ключ активирован!\nПлан: Pro", "server_date": "2025-06-01"}
```

**Error codes:** `invalid_key`, `key_not_found`, `key_blocked`, `key_expired`, `hwid_mismatch`, `no_email`, `unknown_status`

---

## Борьба с Cold Start (Free Tier)

Render Free Tier усыпляет сервис после 15 мин бездействия.  
Cold start = 20–40 секунд.

**Решение:** бесплатный пинг через [UptimeRobot](https://uptimerobot.com):
1. Зарегистрируйтесь на uptimerobot.com
2. New Monitor → HTTP(s)
3. URL: `https://proalbom-license-api.onrender.com/api/health`
4. Interval: **5 minutes**

Это будет держать сервер «горячим» 24/7.

---

## Миграция клиента

После деплоя сервера нужно переключить клиент.

### Шаг 1: Настроить api_client.py

Отредактируйте `VignetteCore/api_client.py`:

```python
API_URLS = [
    "https://proalbom-license-api.onrender.com",  # ← ваш URL с Render
]
API_TOKEN = "ваш_сгенерированный_токен"  # ← тот же что API_SECRET на сервере
```

### Шаг 2: Обновить auth.py

Заменить вызовы gspread на api_client. Основные изменения:

1. Убрать импорт `gspread`, убрать `_open_sheet()`
2. Добавить `from api_client import verify_license, activate_license`
3. В `activate()` — вызывать `activate_license()` вместо прямых записей в таблицу
4. В `ensure_session()` — вызывать `verify_license()` вместо gspread
5. Удалить `credentials.json` с клиентских машин

### Шаг 3: Обновить auth_guard.py

Аналогично: заменить `_verify_online()` на вызов `verify_license()`.

---

## Локальная разработка

```bash
cd license-api
pip install -r requirements.txt

# Задать переменные окружения
set API_SECRET=test123
set GOOGLE_CREDENTIALS_JSON=<содержимое credentials.json>

# Запустить
uvicorn api:app --reload --port 8000

# Тест
curl http://localhost:8000/api/health
```
