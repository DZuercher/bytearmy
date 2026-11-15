"""The page that shows a task's log in a web pane."""

from __future__ import annotations

from html import escape

from bytearmy.models import LogEntry

STYLE = """\
:root { color-scheme: light dark; --text: #1f2328; --muted: #6e7781; --line: #d0d7de;
  --background: #ffffff; }
@media (prefers-color-scheme: dark) {
  :root { --text: #e6edf3; --muted: #8d96a0; --line: #30363d; --background: #0d1117; }
}
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


def render(task_name: str, entries: list[LogEntry]) -> str:
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
        f"<title>Log: {escape(task_name)}</title><style>{STYLE}</style></head>\n"
        f"<body><h1>Log: {escape(task_name)}</h1>\n{body}\n"
        "<script>window.scrollTo(0, document.body.scrollHeight)</script>\n"
        "</body></html>\n"
    )
