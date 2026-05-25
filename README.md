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
   - `QUALITY_EVOLUTION_ENABLED` — включить авто-оценку качества после генерации (`1`/`0`, по умолчанию `1`)
   - `QUALITY_STATE_PATH` — путь к файлу состояния эволюции (по умолчанию `quality_state.json`)
   - `QUALITY_HISTORY_LIMIT` — лимит истории оценок (по умолчанию `200`)
   - `QUALITY_MIN_OVERALL_SCORE` — порог pass/fail для overall score (по умолчанию `4.2`)
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
- `bot.py` — запуск Telegram-бота и web-сервера
- `requirements.txt` — зависимости
- `services.py` — получение транскрипта и multi-agent pipeline
- `telegram_bot.py` — хендлеры Telegram + прогресс генерации
- `quality_agent.py` — авто-оценка и эволюция качества после каждой генерации
- `eval_runner.py` — пакетный benchmark качества
- `eval_dataset.jsonl` — входной датасет для benchmark

## Непрерывная эволюция качества (agents)

После каждой генерации конспекта:
1. Агент-оценщик сравнивает конспект с транскриптом.
2. Считает метрики: `coverage`, `faithfulness`, `structure`, `overall`.
3. Сохраняет историю и running average в `quality_state.json`.
4. Обновляет `style_guidelines` (эволюционные правила).
5. Следующие конспекты получают эти правила в синтез-промпт.

Это создаёт цикл «генерация → оценка → адаптация промпта → генерация лучше».

## Benchmark запуск

1. Заполни `eval_dataset.jsonl` (по 1 JSON-объекту на строку):
   - `{"video_id":"<youtube_video_id>","notes":"..."}`
2. Запусти benchmark:
   - `python eval_runner.py`
3. Результаты:
   - `eval_results.json` — детальные скоры по видео
   - `eval_report.md` — агрегированный отчёт
   - `quality_state.json` — состояние эволюции
