import logging
from html import escape
from flask import Flask

from config import BOT_USERNAME, PORT, SUPABASE
from utils import markdown_to_html, summary_preview

web_app = Flask(__name__)
logger = logging.getLogger(__name__)


def _render_summaries_page(telegram_user_id: int, rows: list[dict]) -> str:
    items_html = ""
    for row in rows:
        url = row.get("video_url") or ""
        created = (row.get("created_at") or "").replace("T", " ")[:19]
        preview = summary_preview(row.get("summary_markdown") or "")
        summary_html = markdown_to_html(row.get("summary_markdown") or "")

        items_html += f"""
        <details class=\"card\">
            <summary class=\"card-summary\">
                <div class=\"card-meta\">
                    <span class=\"meta-date\">{created}</span>
                    <a class=\"video-link\" href=\"{url}\" target=\"_blank\">{url}</a>
                </div>
                <div class=\"card-preview\">{preview}</div>
            </summary>
            <div class=\"card-body\">{summary_html}</div>
        </details>
        """

    if not items_html:
        items_html = "<div class='empty'>Конспектов пока нет.</div>"

    bot_link = escape(f"https://t.me/{BOT_USERNAME}") if BOT_USERNAME else "https://t.me"
    return f"""
    <!doctype html>
    <html lang=\"ru\">
      <head>
        <meta charset=\"utf-8\" />
        <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
        <title>Мои конспекты</title>
        <style>
          :root {{
            color-scheme: light;
            color: #111827;
            background: #f8fafc;
            --bg: #f8fafc;
            --panel: #ffffff;
            --panel-alt: #f3f4f6;
            --border: #e5e7eb;
            --muted: #6b7280;
            --accent: #2563eb;
            --accent-soft: #eff6ff;
          }}
          * {{ box-sizing: border-box; }}
          body {{ margin: 0; font-family: Inter, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: var(--bg); color: #111827; }}
          a {{ color: var(--accent); text-decoration: none; }}
          a:hover {{ text-decoration: underline; }}
          .wrap {{ max-width: 1120px; margin: 24px auto; padding: 16px; }}
          .header {{ display: flex; flex-wrap: wrap; justify-content: space-between; gap: 16px; align-items: center; margin-bottom: 20px; }}
          .title-block {{ display: grid; gap: 8px; }}
          h1 {{ margin: 0; font-size: clamp(2rem, 3vw, 2.4rem); }}
          .subtitle {{ margin: 0; color: var(--muted); max-width: 720px; }}
          .sidebar {{ display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }}
          .button {{ display: inline-flex; align-items: center; gap: 8px; padding: 12px 16px; border-radius: 12px; border: 1px solid var(--border); background: var(--panel); color: #111827; font-weight: 600; }}
          .button:hover {{ background: var(--accent-soft); border-color: var(--accent); }}
          .grid {{ display: grid; gap: 16px; }}
          .card {{ background: var(--panel); border: 1px solid var(--border); border-radius: 18px; overflow: hidden; box-shadow: 0 14px 32px rgba(15, 23, 42, 0.06); }}
          summary.card-summary {{ list-style: none; cursor: pointer; padding: 20px; }}
          summary.card-summary::-webkit-details-marker {{ display: none; }}
          .card-meta {{ display: flex; flex-wrap: wrap; gap: 12px; align-items: center; }}
          .meta-date {{ color: var(--muted); font-size: 0.95rem; }}
          .video-link {{ font-size: 0.98rem; font-weight: 600; color: #1d4ed8; word-break: break-all; }}
          .card-preview {{ margin-top: 12px; color: #334155; line-height: 1.7; min-height: 60px; }}
          .card-body {{ padding: 0 20px 20px; border-top: 1px solid var(--border); background: var(--panel-alt); }}
          .card-body p {{ margin: 0 0 14px; line-height: 1.8; }}
          .card-body ul {{ margin: 0 0 14px 20px; padding: 0; }}
          .card-body li {{ margin-bottom: 10px; }}
          .empty {{ padding: 28px 24px; border: 1px dashed var(--border); border-radius: 18px; color: var(--muted); text-align: center; background: var(--panel); }}
          @media (max-width: 820px) {{ .header {{ flex-direction: column; align-items: stretch; }} .card-body {{ padding: 0 16px 16px; }} }}
        </style>
      </head>
      <body>
        <div class=\"wrap\">
          <div class=\"header\">
            <div class=\"title-block\">
              <h1>Конспекты пользователя #{telegram_user_id}</h1>
              <p class=\"subtitle\">Здесь хранятся переформулированные конспекты видео в удобном формате. Раскрывай карточки, чтобы увидеть полный текст.</p>
            </div>
            <div class=\"sidebar\">
              <a class=\"button\" href=\"{bot_link}\">↩ Вернуться в бота</a>
            </div>
          </div>
          <div class=\"grid\">{items_html}</div>
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
