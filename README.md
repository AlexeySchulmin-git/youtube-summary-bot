# YouTube Summary Bot

## Деплой на Railway

1. Создай аккаунт на [railway.app](https://railway.app)
2. Нажми **New Project** → **Deploy from GitHub repo**
3. Залей эти файлы в GitHub репозиторий
4. В Railway перейди в **Variables** и добавь:
   - `TELEGRAM_TOKEN` — токен от BotFather
   - `GEMINI_API_KEY` — ключ от Google AI Studio
5. Railway сам установит зависимости и запустит бота

## Файлы
- `bot.py` — основной код бота
- `requirements.txt` — зависимости
- `Procfile` — команда запуска для Railway
