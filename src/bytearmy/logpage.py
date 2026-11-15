"""The page that shows a task's log in a web pane."""

from __future__ import annotations

from html import escape

from bytearmy.models import LogEntry, Theme

STYLE = """\
body { margin: 0; padding: 16px; background: var(--background); color: var(--text);
  font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
h1 { font-size: 16px; margin: 0 0 12px; }
table { border-collapse: collapse; width: 100%; }
td { border-top: 1px solid var(--line); padding: 6px 12px 6px 0; vertical-align: top; }
td.time, td.author { color: var(--muted); white-space: nowrap;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
td.text { white-space: pre-wrap; overflow-wrap: anywhere; width: 100%; }
p { color: var(--muted); }
"""


def colors(theme: Theme) -> str:
    """The page's colors, from the color scheme of the window."""
    shown = {
        "text": "foreground",
        "muted": "muted-foreground",
        "line": "border",
        "background": "background",
    }
    variables = " ".join(f"--{own}: {theme.colors[name]};" for own, name in shown.items())
    return f":root {{ color-scheme: {theme.appearance}; {variables} }}\n"


def render(task_name: str, entries: list[LogEntry], theme: Theme) -> str:
    """The log as an HTML page, oldest entry first; it opens scrolled to the newest."""
    rows = "\n".join(
        "<tr>"
        f'<td class="time">{entry.time.strftime("%Y-%m-%d %H:%M:%S")} UTC</td>'
        f'<td class="author">{escape(entry.author)}</td>'
        f'<td class="text">{escape(entry.text)}</td>'
        "</tr>"
        for entry in entries
    )
    body = f"<table>\n{rows}\n</table>" if entries else "<p>No entries yet.</p>"
    return (
        "<!doctype html>\n"
        '<html><head><meta charset="utf-8">'
        f"<title>Log: {escape(task_name)}</title><style>{colors(theme)}{STYLE}</style></head>\n"
        f"<body><h1>Log: {escape(task_name)}</h1>\n{body}\n"
        "<script>window.scrollTo(0, document.body.scrollHeight)</script>\n"
        "</body></html>\n"
    )
