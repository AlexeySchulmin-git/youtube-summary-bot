import logging
from html import escape

from flask import Flask

from config import BOT_USERNAME, PORT, SUPABASE
from utils import markdown_to_html, summary_preview_html

web_app = Flask(__name__)
logger = logging.getLogger(__name__)


def _render_summaries_page(telegram_user_id: int, rows: list[dict]) -> str:
    items_html = ""
    for row in rows:
        url = escape(row.get("video_url") or "")
        created = escape((row.get("created_at") or "").replace("T", " ")[:19])
        raw_summary = row.get("summary_markdown") or ""
        preview_html = summary_preview_html(raw_summary)
        summary_markdown = raw_summary.replace("✅ **Кому это важно**", "🧭 **Вывод**")
        summary_html = markdown_to_html(summary_markdown)

        items_html += f"""
        <details class="card">
          <summary class="card-summary">
            <div class="card-meta">
              <span class="meta-date">🕒 {created}</span>
              <a class="video-link" href="{url}" target="_blank" rel="noopener noreferrer">{url}</a>
            </div>
            <div class="card-preview">{preview_html}</div>
          </summary>
          <div class="card-body">{summary_html}</div>
        </details>
        """

    if not items_html:
        items_html = "<div class='empty'>Конспектов пока нет.</div>"

    bot_link = escape(f"https://t.me/{BOT_USERNAME}") if BOT_USERNAME else "https://t.me"

    return f"""
    <!doctype html>
    <html lang="ru">
      <head>
        <meta charset="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>Мои конспекты</title>
        <link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='14' fill='%232563eb'/%3E%3Cpath d='M22 20h6l6 16 6-16h6l-10 24h-4z' fill='white'/%3E%3C/svg%3E">
        <style>
          :root {{
            --bg: #f3f4f6;
            --shell: #ffffff;
            --panel: #ffffff;
            --panel-soft: #f8fafc;
            --border: #e5e7eb;
            --text: #111827;
            --muted: #6b7280;
            --accent: #2563eb;
            --accent-soft: #eff6ff;
          }}
          * {{ box-sizing: border-box; }}
          body {{ margin: 0; font-family: Inter, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); }}
          a {{ color: var(--accent); text-decoration: none; }}
          a:hover {{ text-decoration: underline; }}

          .app {{ max-width: 1160px; margin: 28px auto; padding: 0 16px; }}
          .shell {{
            background: var(--shell);
            border: 1px solid var(--border);
            border-radius: 24px;
            overflow: hidden;
            box-shadow: 0 20px 60px rgba(15, 23, 42, 0.08);
            min-height: 84vh;
            display: grid;
            grid-template-columns: 250px minmax(0, 1fr);
          }}

          .left {{ border-right: 1px solid var(--border); background: #fcfcfd; padding: 18px 14px; display: flex; flex-direction: column; gap: 18px; }}
          .brand {{ font-weight: 700; font-size: 1.1rem; padding: 10px 8px; }}
          .menu-title {{ color: var(--muted); font-size: 0.75rem; letter-spacing: .06em; text-transform: uppercase; padding: 0 8px; }}
          .menu {{ display: grid; gap: 6px; }}
          .menu a {{ color: #1f2937; padding: 10px 12px; border-radius: 10px; font-weight: 500; }}
          .menu a.active {{ background: var(--accent-soft); color: #1d4ed8; }}
          .left-footer {{ margin-top: auto; padding: 8px; }}
          .button {{ display: inline-flex; align-items: center; gap: 8px; padding: 10px 14px; border-radius: 12px; border: 1px solid var(--border); background: #fff; color: #111827; font-weight: 600; }}
          .button:hover {{ background: var(--accent-soft); border-color: var(--accent); }}

          .content {{ padding: 22px 24px; }}
          .header {{ margin-bottom: 16px; }}
          .title-block {{ display: grid; gap: 10px; }}
          h1 {{ margin: 0; font-size: clamp(1.8rem, 2.6vw, 2.35rem); line-height: 1.15; }}
          .subtitle {{ margin: 0; color: var(--muted); max-width: 720px; line-height: 1.5; }}
          .tags {{ display: flex; flex-wrap: wrap; gap: 8px; }}
          .tag {{ border: 1px solid var(--border); background: #f9fafb; padding: 6px 10px; border-radius: 999px; font-size: .82rem; color: #374151; }}

          .grid {{ display: grid; gap: 14px; }}
          .card {{ background: var(--panel); border: 1px solid var(--border); border-radius: 16px; overflow: hidden; }}
          summary.card-summary {{ list-style: none; cursor: pointer; padding: 16px 18px; background: #fff; }}
          summary.card-summary::-webkit-details-marker {{ display: none; }}
          .card-meta {{ display: flex; flex-wrap: wrap; gap: 12px; align-items: center; }}
          .meta-date {{ color: var(--muted); font-size: 0.9rem; }}
          .video-link {{ font-size: 0.95rem; font-weight: 600; color: #1d4ed8; word-break: break-all; }}
          .card-preview {{ margin-top: 10px; color: #334155; line-height: 1.65; min-height: 36px; font-size: calc(0.95rem + 2px); }}
          .preview-title {{ font-weight: 700; color: #0f172a; margin-right: 6px; }}
          .card-body {{ padding: 16px 18px 18px; border-top: 1px solid var(--border); background: var(--panel-soft); }}
          .card-body p {{ margin: 0 0 14px; line-height: 1.8; }}
          .card-body ul {{ margin: 0 0 14px 20px; padding: 0; }}
          .card-body li {{ margin-bottom: 10px; }}
          .empty {{ padding: 28px 24px; border: 1px dashed var(--border); border-radius: 18px; color: var(--muted); text-align: center; background: var(--panel); }}

          @media (max-width: 820px) {{
            .shell {{ grid-template-columns: 1fr; }}
            .left {{ border-right: 0; border-bottom: 1px solid var(--border); }}
            .content {{ padding: 16px; }}
            .card-body {{ padding: 14px 14px 16px; }}
          }}
        </style>
      </head>
      <body>
        <div class="app">
          <div class="shell">
            <aside class="left">
              <div class="brand">📝 YouTube Summary</div>
              <div class="menu-title">Main menu</div>
              <nav class="menu">
                <a class="active" href="#">Конспекты</a>
                <a href="{bot_link}">Вернуться в бота</a>
              </nav>
              <div class="left-footer">
                <a class="button" href="{bot_link}">↩ Открыть Telegram</a>
              </div>
            </aside>

            <main class="content">
              <div class="header">
                <div class="title-block">
                  <h1>Конспекты пользователя #{telegram_user_id}</h1>
                  <p class="subtitle">Лента конспектов: сверху выделено краткое резюме, внутри карточки — полный структурированный текст.</p>
                  <div class="tags">
                    <span class="tag">YouTube</span>
                    <span class="tag">AI Summary</span>
                    <span class="tag">User #{telegram_user_id}</span>
                  </div>
                </div>
              </div>
              <div class="grid">{items_html}</div>
            </main>
          </div>
        </div>
      </body>
    </html>
    """


@web_app.get("/u/<int:telegram_user_id>")
def user_summaries_page(telegram_user_id: int):
    if not SUPABASE:
        return "Supabase не настроен", 500

    try:
        profile_resp = (
            SUPABASE.table("user_profiles")
            .select("id")
            .eq("telegram_user_id", telegram_user_id)
            .limit(1)
            .execute()
        )
        if not profile_resp.data:
            return _render_summaries_page(telegram_user_id, [])

        user_id = profile_resp.data[0]["id"]
        summaries_resp = (
            SUPABASE.table("summaries")
            .select("video_url, summary_markdown, created_at")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(30)
            .execute()
        )
        rows = summaries_resp.data or []
        return _render_summaries_page(telegram_user_id, rows)
    except Exception as exc:
        logger.warning(f"Summaries page failed: {exc}")
        return "Ошибка загрузки страницы", 500


@web_app.get("/")
def index_page():
    return "OK", 200


def run_web_server():
    logger.info(f"Starting Flask web server on port {PORT}")
    web_app.run(host="0.0.0.0", port=PORT)
