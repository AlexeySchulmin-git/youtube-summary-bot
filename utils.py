import re
from html import escape


def markdown_to_html(markdown_text: str) -> str:
    escaped = escape(markdown_text or "")
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"\*(.+?)\*", r"<em>\1</em>", escaped)
    escaped = re.sub(r"`(.+?)`", r"<code>\1</code>", escaped)

    html_lines: list[str] = []
    in_list = False
    for raw_line in escaped.splitlines():
        line = raw_line.strip()
        if not line:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            continue

        if re.match(r"^([-*])\s+", line) or re.match(r"^\d+[.)]\s+", line):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            item = re.sub(r"^([-*]|\d+[.)])\s+", "", line)
            html_lines.append(f"<li>{item}</li>")
            continue

        if in_list:
            html_lines.append("</ul>")
            in_list = False

        html_lines.append(f"<p>{line}</p>")

    if in_list:
        html_lines.append("</ul>")

    return "\n".join(html_lines)


def summary_preview(summary_text: str, max_length: int = 220) -> str:
    tidy = re.sub(r"\s+", " ", summary_text or "").strip()
    if len(tidy) <= max_length:
        return tidy
    shortened = tidy[:max_length].rsplit(" ", 1)[0]
    return f"{shortened}…"
