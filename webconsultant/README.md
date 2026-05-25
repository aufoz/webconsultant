# WebConsultant — AI-консультант для сайтов

Полностью бесплатный веб-сервис: вставляешь URL → сайт сканируется → чат отвечает как консультант.

## Стек (всё бесплатное и open source)

| Компонент | Технология |
|-----------|------------|
| Backend | Python + FastAPI |
| Краулер | requests + BeautifulSoup |
| База данных | SQLite (файл, без сервера) |
| AI (опционально) | Ollama + llama3.2 (локально) |
| Поиск по базе | Keyword scoring (встроенный) |
| Frontend | HTML + CSS + JS (без фреймворков) |

---

## Быстрый старт

### 1. Требования
- Python 3.10+
- pip

### 2. Установка и запуск

```bash
cd webconsultant
chmod +x start.sh
./start.sh
```

Или вручную:
```bash
pip install -r requirements.txt
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

Открой браузер: **http://localhost:8000**

---

## Режимы работы

### Без AI (по умолчанию)
Работает сразу. Поиск по ключевым словам — находит релевантные страницы и отвечает фрагментами текста.

### С Ollama AI (рекомендуется)
Даёт полноценные связные ответы.

```bash
# Установить Ollama
curl https://ollama.ai/install.sh | sh

# Скачать модель (2.0 GB)
ollama pull llama3.2

# Установить python-библиотеку
pip install ollama

# Запустить сервис
./start.sh
```

Модели на выбор:
- `llama3.2` — баланс скорости и качества (рекомендуется)
- `mistral` — хорош для русского языка
- `phi3` — быстрый, лёгкий (1.7 GB)

---

## Структура проекта

```
webconsultant/
├── backend/
│   └── main.py          # FastAPI: сканер, API, поиск, AI
├── frontend/
│   ├── index.html       # Главная страница
│   └── static/
│       ├── css/style.css
│       └── js/app.js
├── requirements.txt
├── start.sh
└── data.db              # SQLite (создаётся автоматически)
```

---

## API эндпоинты

| Метод | URL | Описание |
|-------|-----|----------|
| POST | /api/scan | Сканировать сайт |
| POST | /api/chat | Отправить вопрос |
| GET | /api/sites | Список сайтов |
| GET | /api/stats/{id} | Статистика сайта |
| DELETE | /api/sites/{id} | Удалить сайт |

---

## Деплой на сервер (бесплатно)

### Railway.app
```bash
# В корне добавить Procfile:
echo "web: uvicorn backend.main:app --host 0.0.0.0 --port \$PORT" > Procfile
# Залить на GitHub → подключить в Railway
```

### Render.com
- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn backend.main:app --host 0.0.0.0 --port $PORT`

### VPS (Ubuntu)
```bash
pip install -r requirements.txt
pip install gunicorn
gunicorn backend.main:app -w 2 -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8000
```

---

## Что можно улучшить

- **Векторный поиск**: заменить keyword scoring на sentence-transformers + FAISS (бесплатно, локально)
- **Планировщик**: автоматически пересканировать сайты раз в неделю
- **Виджет для сайта**: embed-чат как у Intercom (один `<script>` тег)
- **Мультиязычность**: Ollama хорошо понимает русский
- **Авторизация**: добавить логин для SaaS-версии

---

## Лицензия
MIT — делайте что хотите.
