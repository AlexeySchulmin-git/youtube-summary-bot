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
   - `SUPABASE_URL` — URL проекта Supabase (Settings → API → Project URL)
   - `SUPABASE_SERVICE_ROLE_KEY` — Service Role key (Settings → API → service_role, хранить только на backend)
   - `ANALYST_MODEL_SMALL` — модель для коротких чанков (опционально)
   - `ANALYST_MODEL_LARGE` — модель для длинных чанков (опционально)
   - `SYNTHESIZER_MODEL` — модель для финального синтеза (опционально)
   - `CHUNK_TARGET_TOKENS` — целевой размер чанка, по умолчанию `2500` (опционально)
   - `CHUNK_MAX_TOKENS` — максимум токенов чанка, по умолчанию `3000` (опционально)
   - `CHUNK_OVERLAP_TOKENS` — overlap токенов, по умолчанию `200` (опционально)
5. Нажми **Save Changes** и сделай redeploy сервиса

## Порядок получения субтитров

1. `youtube-transcript-api` (основной)
2. `SUPADATA` (fallback)

## Supabase: создание таблиц (пока MVP)

В проект добавлен файл `supabase/schema.sql`.

Как применить в VS Code (через Supabase extension):

1. Подключи extension к нужному проекту Supabase
2. Открой `supabase/schema.sql`
3. Выполни SQL в подключенной БД (Run Query)

Что создаётся:
- `public.user_profiles` — пользователи Telegram
- `public.summaries` — сохранённые конспекты
- `public.summary_feedback` — оценка качества пользователем

## Файлы
- `bot.py` — основной код бота
- `requirements.txt` — зависимости
- `Procfile` — команда запуска для Railway
