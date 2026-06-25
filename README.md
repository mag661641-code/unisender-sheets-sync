# Unisender → Google Sheets

Автоматический перенос статистики email-рассылок из Unisender в Google Sheets.

## Что делает

Скрипт находит в Google Sheet строки где заполнены дата, сегмент и тема рассылки, но пустая графа «Отправлено» — и автоматически переносит туда статистику из Unisender: отправлено, доставлено, прочитано, переходы, отписки, спам, недоставлено, тип устройства, день недели.

## Запуск локально

### 1. Установите зависимости

```
pip install streamlit selenium gspread google-auth
```

### 2. Создайте Google Service Account

1. Перейдите в [Google Cloud Console](https://console.cloud.google.com/)
2. Создайте проект → включите Google Sheets API и Google Drive API
3. Создайте Service Account → скачайте JSON-ключ → сохраните как `credentials.json` в папку проекта
4. Откройте вашу Google Sheet → Настройки доступа → добавьте email сервисного аккаунта из `credentials.json` с ролью **Редактор**

### 3. Настройте аккаунты

Создайте файл `accounts.json`:

```json
[
  {
    "name": "Название аккаунта",
    "unisender_email": "your@email.com",
    "unisender_password": "your_password",
    "sheet_name": "Название Google Sheet",
    "worksheet": "Название листа",
    "cols": {
      "sent": "G",
      "delivered": "H",
      "delivered_pct": "I",
      "opened": "J",
      "opened_pct": "K",
      "ctr_unique": "L",
      "ctr_pct": "M",
      "clicks_total": "N",
      "clicks_pct": "O",
      "unsub": "P",
      "unsub_pct": "Q",
      "spam": "R",
      "spam_pct": "S",
      "undelivered": "T",
      "desktop": "U",
      "tablet": "V",
      "mobile": "W",
      "weekday": "X",
      "time": "Y"
    }
  }
]
```

### 4. Запустите

```
python -m streamlit run unisender_app.py
```

---

## Деплой на Streamlit Cloud

### 1. Залейте репозиторий на GitHub

Убедитесь что в `.gitignore` есть:
```
credentials.json
accounts.json
.streamlit/secrets.toml
```

### 2. Подключите репозиторий в [Streamlit Cloud](https://streamlit.io/cloud)

Укажите файл запуска: `unisender_app.py`

### 3. Добавьте секреты

В Streamlit Cloud: **App → Settings → Secrets** — вставьте и заполните:

```toml
[proxy]
user     = "логин_прокси"
password = "пароль_прокси"
host     = "5.42.209.148"
port     = "64328"

[gcp_service_account]
type                        = "service_account"
project_id                  = "..."
private_key_id              = "..."
private_key                 = "-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----\n"
client_email                = "...@....iam.gserviceaccount.com"
client_id                   = "..."
auth_uri                    = "https://accounts.google.com/o/oauth2/auth"
token_uri                   = "https://oauth2.googleapis.com/token"
auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
client_x509_cert_url        = "..."

[[accounts]]
name               = "Название аккаунта"
unisender_email    = "your@email.com"
unisender_password = "your_password"
sheet_name         = "Название Google Sheet"
worksheet          = "Название листа"

[accounts.cols]
sent          = "G"
delivered     = "H"
delivered_pct = "I"
opened        = "J"
opened_pct    = "K"
ctr_unique    = "L"
ctr_pct       = "M"
clicks_total  = "N"
clicks_pct    = "O"
unsub         = "P"
unsub_pct     = "Q"
spam          = "R"
spam_pct      = "S"
undelivered   = "T"
desktop       = "U"
tablet        = "V"
mobile        = "W"
weekday       = "X"
time          = "Y"
```

Значения для `[gcp_service_account]` берутся напрямую из скачанного файла `credentials.json`.

---

## Структура проекта

```
├── unisender_app.py      # Streamlit-интерфейс
├── parser_core.py        # Логика парсера
├── unisender_parser.py   # Запуск из терминала (без интерфейса)
├── accounts.json         # Аккаунты (локально, не в git)
├── credentials.json      # Google ключ (локально, не в git)
├── packages.txt          # Зависимости для Streamlit Cloud (Chrome)
└── .streamlit/
    └── config.toml       # Светлая тема
```

## Используемые технологии

- [Selenium](https://selenium-python.readthedocs.io/) — автоматизация браузера
- [gspread](https://docs.gspread.org/) — работа с Google Sheets
- [Streamlit](https://streamlit.io/) — веб-интерфейс
