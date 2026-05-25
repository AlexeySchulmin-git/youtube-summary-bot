import logging
from html import escape

from flask import Flask

from config import BOT_USERNAME, PORT, SUPABASE
from utils import markdown_to_html, summary_preview

web_app = Flask(__name__)
logger = logging.getLogger(__name__)


def _render_comment_item(author: str, ago: str, text: str) -> str:
    return f"""
    <article class=\"comment\">
      <div class=\"comment-head\">
        <div class=\"avatar\">{escape(author[:1].upper())}</div>
        <div>
          <div class=\"comment-author\">{escape(author)}</div>
          <div class=\"comment-time\">{escape(ago)}</div>
        </div>
      </div>
      <p class=\"comment-text\">{escape(text)}</p>
      <div class=\"comment-actions\">👍 👎 Reply</div>
    </article>
    """


def _render_summaries_page(telegram_user_id: int, rows: list[dict]) -> str:
    items_html = ""
    for row in rows:
        url = escape(row.get("video_url") or "")
        created = escape((row.get("created_at") or "").replace("T", " ")[:19])
        preview = escape(summary_preview(row.get("summary_markdown") or ""))
        summary_html = markdown_to_html(row.get("summary_markdown") or "")

        items_html += f"""
        <details class=\"card\" open>
          <summary class=\"card-summary\">
            <div class=\"card-meta\">
              <span class=\"meta-date\">🕒 {created}</span>
              <a class=\"video-link\" href=\"{url}\" target=\"_blank\" rel=\"noopener noreferrer\">{url}</a>
            </div>
            <div class=\"card-preview\">{preview}</div>
          </summary>
          <div class=\"card-body\">{summary_html}</div>
        </details>
        """

    if not items_html:
        items_html = "<div class='empty'>Конспектов пока нет.</div>"

    comments_html = "".join(
        [
            _render_comment_item("Azahra Ge", "2 days ago", "Updated spacing in note cards and improved readability."),
            _render_comment_item("Arbian Musaf", "2 days ago", "Typography tune-up is in progress. Final polish next."),
            _render_comment_item("Azahra Ge", "1 day ago", "Reviewed alignment in dashboard blocks and side panel."),
        ]
    )

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

          .app {{ max-width: 1380px; margin: 28px auto; padding: 0 16px; }}
          .shell {{
            background: var(--shell);
            border: 1px solid var(--border);
            border-radius: 24px;
            overflow: hidden;
            box-shadow: 0 20px 60px rgba(15, 23, 42, 0.08);
            min-height: 84vh;
            display: grid;
            grid-template-columns: 250px minmax(0, 1fr) 320px;
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

          .main {{ padding: 0; background: #fff; }}
          .toolbar {{ height: 64px; border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: space-between; gap: 14px; padding: 0 18px; }}
          .toolbar-left {{ display: flex; align-items: center; gap: 12px; min-width: 0; }}
          .search {{ display: flex; align-items: center; gap: 8px; border: 1px solid var(--border); border-radius: 10px; padding: 8px 10px; min-width: 290px; background: #fff; color: #6b7280; }}
          .search input {{ border: 0; outline: none; width: 100%; font-size: .92rem; color: #374151; }}
          .toolbar-right {{ display: flex; align-items: center; gap: 8px; }}
          .icon-btn {{ width: 34px; height: 34px; border: 1px solid var(--border); border-radius: 10px; display: inline-flex; align-items: center; justify-content: center; background: #fff; color: #374151; font-size: .95rem; }}

          .content {{ padding: 22px 24px; }}
          .header {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; margin-bottom: 16px; }}
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
          .card-preview {{ margin-top: 10px; color: #334155; line-height: 1.65; min-height: 36px; }}
          .card-body {{ padding: 16px 18px 18px; border-top: 1px solid var(--border); background: var(--panel-soft); }}
          .card-body p {{ margin: 0 0 14px; line-height: 1.8; }}
          .card-body ul {{ margin: 0 0 14px 20px; padding: 0; }}
          .card-body li {{ margin-bottom: 10px; }}
          .empty {{ padding: 28px 24px; border: 1px dashed var(--border); border-radius: 18px; color: var(--muted); text-align: center; background: var(--panel); }}

          .right {{ border-left: 1px solid var(--border); background: #fcfcfd; display: flex; flex-direction: column; min-height: 100%; }}
          .comments-top {{ height: 64px; border-bottom: 1px solid var(--border); padding: 0 14px; display: flex; align-items: center; justify-content: space-between; }}
          .comments-title {{ font-weight: 700; font-size: 0.95rem; }}
          .comments-body {{ padding: 12px; display: grid; gap: 10px; }}
          .comments-search {{ border: 1px solid var(--border); border-radius: 10px; padding: 8px 10px; color: #6b7280; font-size: .9rem; background: #fff; }}
          .comment {{ border: 1px solid var(--border); border-radius: 12px; padding: 10px; background: #fff; }}
          .comment-head {{ display: flex; gap: 8px; align-items: center; margin-bottom: 6px; }}
          .avatar {{ width: 24px; height: 24px; border-radius: 50%; background: #dbeafe; color: #1d4ed8; display: inline-flex; align-items: center; justify-content: center; font-size: .78rem; font-weight: 700; }}
          .comment-author {{ font-size: .86rem; font-weight: 600; color: #111827; }}
          .comment-time {{ font-size: .75rem; color: var(--muted); }}
          .comment-text {{ margin: 0 0 8px; font-size: .86rem; line-height: 1.45; color: #374151; }}
          .comment-actions {{ font-size: .78rem; color: var(--muted); }}

          @media (max-width: 1180px) {{
            .shell {{ grid-template-columns: 220px minmax(0, 1fr); }}
            .right {{ display: none; }}
          }}
          @media (max-width: 820px) {{
            .shell {{ grid-template-columns: 1fr; }}
            .left {{ border-right: 0; border-bottom: 1px solid var(--border); }}
            .search {{ min-width: 180px; }}
            .content {{ padding: 16px; }}
            .card-body {{ padding: 14px 14px 16px; }}
          }}
        </style>
      </head>
      <body>
        <div class=\"app\">
          <div class=\"shell\">
            <aside class=\"left\">
              <div class=\"brand\">📝 YouTube Summary</div>
              <div class=\"menu-title\">Main menu</div>
              <nav class=\"menu\">
                <a class=\"active\" href=\"#\">Конспекты</a>
                <a href=\"{bot_link}\">Вернуться в бота</a>
              </nav>
              <div class=\"left-footer\">
                <a class=\"button\" href=\"{bot_link}\">↩ Открыть Telegram</a>
              </div>
            </aside>

            <main class=\"main\">
              <div class=\"toolbar\">
                <div class=\"toolbar-left\">
                  <span class=\"icon-btn\">☰</span>
                  <label class=\"search\">🔎 <input type=\"text\" placeholder=\"Search anything\" /></label>
                </div>
                <div class=\"toolbar-right\">
                  <span class=\"icon-btn\">🔔</span>
                  <span class=\"icon-btn\">⚙️</span>
                  <span class=\"icon-btn\">＋</span>
                </div>
              </div>

              <div class=\"content\">
                <div class=\"header\">
                  <div class=\"title-block\">
                    <h1>Конспекты пользователя #{telegram_user_id}</h1>
                    <p class=\"subtitle\">Удобная рабочая лента конспектов: сверху краткий preview, внутри карточки — полный структурированный текст.</p>
                    <div class=\"tags\">
                      <span class=\"tag\">YouTube</span>
                      <span class=\"tag\">AI Summary</span>
                      <span class=\"tag\">User #{telegram_user_id}</span>
                    </div>
                  </div>
                </div>
                <div class=\"grid\">{items_html}</div>
              </div>
            </main>

            <aside class=\"right\">
              <div class=\"comments-top\">
                <div class=\"comments-title\">Comments</div>
                <span class=\"icon-btn\">✕</span>
              </div>
              <div class=\"comments-body\">
                <div class=\"comments-search\">🔎 Search comments</div>
                {comments_html}
              </div>
            </aside>
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
