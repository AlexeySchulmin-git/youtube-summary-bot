# YouTube Summary Bot

## Деплой на Render (через GitHub)

1. Создай аккаунт на [render.com](https://render.com)
2. Нажми **New** → **Web Service** → подключи GitHub-репозиторий
3. Выбери ветку `main` и команду запуска бота
4. В Render открой **Environment** и добавь:
   - `TELEGRAM_TOKEN` — токен от BotFather
   - `OPENAI_API_KEY` — ключ для OpenAI-совместимого API
   - `OPENAI_BASE_URL` — базовый URL OpenAI-совместимого API (опционально)
   - `OPENAI_MODEL` — модель для суммаризации (опционально)
   - `SUPADATA_API_KEY` — ключ Supadata (используется как fallback)
5. Нажми **Save Changes** и сделай redeploy сервиса

## Порядок получения субтитров

1. `youtube-transcript-api` (основной)
2. `SUPADATA` (fallback)

## Файлы
- `bot.py` — основной код бота
- `requirements.txt` — зависимости
- `Procfile` — команда запуска для Railway
