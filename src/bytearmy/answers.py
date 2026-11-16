"""Answers to GitHub conversations that an agent drafts and the user approves.

`bytearmy answer draft <url>` fetches the conversation of a comment with gh and shows it in
an answer pane, with the agent's draft below it for the user to edit. The user approves or
discards it there; either way the agent that drafted it is told. Once approved, the agent
makes the changes the answer promises and posts it with `bytearmy answer send`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from html import escape
from pathlib import Path

from bytearmy.logpage import colors
from bytearmy.models import Draft, DraftComment, Theme
from bytearmy.prs import GitHubFailed, run_in_clone
from bytearmy.store import METADATA_DIR

DRAFTS_DIR = "answers"  # below the task's metadata: one JSON file per draft, by its pane
URL = re.compile(
    r"https?://(?P<host>[^/\s]+)/(?P<owner>[^/\s]+)/(?P<repo>[^/\s]+)/"
    r"(?P<kind>pull|issues)/(?P<number>\d+)(?:/[^#\s]*)?(?:#(?P<anchor>\S*))?$"
)
ISSUE_COMMENT = re.compile(r"issuecomment-(\d+)$")
REVIEW_COMMENT = re.compile(r"(?:discussion_)?r(\d+)$")
FIELDS = "{id, in_reply_to_id, user: .user.login, body, created_at, html_url, path, diff_hunk}"


class AnswerFailed(GitHubFailed):
    """The conversation could not be read or answered; the message says why."""


@dataclass(frozen=True)
class Target:
    """The conversation a GitHub URL points at."""

    host: str
    owner: str
    repo: str
    number: int
    thread: str  # "issue": the comments of an issue or pull request; "review": a code thread
    comment: int | None  # the comment the URL names, if any


def parse_url(url: str) -> Target:
    """The conversation of a comment's URL, an issue's or a pull request's."""
    match = URL.match(url.strip())
    if match is None:
        raise AnswerFailed(
            f"{url} is not the URL of a GitHub issue, pull request or comment, such as "
            "https://github.com/owner/repo/pull/12#issuecomment-345"
        )
    anchor = match["anchor"] or ""
    thread, comment = "issue", None
    if found := ISSUE_COMMENT.match(anchor):
        comment = int(found[1])
    elif match["kind"] == "pull" and (found := REVIEW_COMMENT.match(anchor)):
        thread, comment = "review", int(found[1])
    return Target(
        match["host"], match["owner"], match["repo"], int(match["number"]), thread, comment
    )


class AnswerCommands:
    """Reads and answers conversations with `gh api`; gh can be replaced for tests."""

    def __init__(self, gh: Sequence[str] = ("gh",)) -> None:
        self._gh = list(gh)

    def conversation(
        self, url: str, directory: Path, env: Mapping[str, str], draft: str, agent: str
    ) -> Draft:
        """A new draft of `agent`: the conversation `url` points at, and the answer."""
        target = parse_url(url)
        base = f"repos/{target.owner}/{target.repo}"
        issue = self._json(
            [
                f"{base}/issues/{target.number}",
                "--jq",
                "{title, user: .user.login, body, created_at, html_url}",
            ],
            directory,
            env,
            target,
        )
        fields = {
            "id": "",
            "url": url.strip(),
            "title": issue.get("title") or "",
            "page": issue.get("html_url") or "",
            "owner": target.owner,
            "repo": target.repo,
            "host": target.host,
            "number": target.number,
            "thread": target.thread,
            "draft": draft,
            "agent": agent,
        }
        if target.thread == "issue":
            listed = self._lines(
                [
                    f"{base}/issues/{target.number}/comments",
                    "--paginate",
                    "--jq",
                    f".[] | {FIELDS}",
                ],
                directory,
                env,
                target,
            )
            fields["comments"] = [_comment(issue), *(_comment(item) for item in listed)]
            fields["focus"] = str(target.comment or "")
            return Draft(**fields)
        named = self._json(
            [f"{base}/pulls/comments/{target.comment}", "--jq", FIELDS], directory, env, target
        )
        root = named.get("in_reply_to_id") or named.get("id")
        listed = self._lines(
            [f"{base}/pulls/{target.number}/comments", "--paginate", "--jq", f".[] | {FIELDS}"],
            directory,
            env,
            target,
        )
        thread = [item for item in listed if root in (item.get("id"), item.get("in_reply_to_id"))]
        first = next((item for item in thread if item.get("id") == root), named)
        fields.update(
            comments=[_comment(item) for item in thread] or [_comment(named)],
            focus=str(target.comment),
            reply_to=root,
            path=first.get("path") or "",
            diff_hunk=first.get("diff_hunk") or "",
        )
        return Draft(**fields)

    def send(self, draft: Draft, directory: Path, env: Mapping[str, str]) -> str:
        """Post the approved answer to its conversation; gives the new comment's URL."""
        base = f"repos/{draft.owner}/{draft.repo}"
        if draft.thread == "review":
            path = f"{base}/pulls/{draft.number}/comments/{draft.reply_to}/replies"
        else:
            path = f"{base}/issues/{draft.number}/comments"
        target = Target(draft.host, draft.owner, draft.repo, draft.number, draft.thread, None)
        command = [path, "--method", "POST", "-f", f"body={draft.answer}", "--jq", ".html_url"]
        return self._run(command, directory, env, target).strip()

    def _run(
        self, arguments: list[str], directory: Path, env: Mapping[str, str], target: Target
    ) -> str:
        host = [] if target.host == "github.com" else ["--hostname", target.host]
        command = [*self._gh, "api", *host, *arguments]
        return run_in_clone(command, directory, env, "gh api", AnswerFailed)

    def _json(
        self, arguments: list[str], directory: Path, env: Mapping[str, str], target: Target
    ) -> dict:
        try:
            return json.loads(self._run(arguments, directory, env, target))
        except ValueError as error:
            raise AnswerFailed(f"gh api answered in an unknown form: {error}") from error

    def _lines(
        self, arguments: list[str], directory: Path, env: Mapping[str, str], target: Target
    ) -> list[dict]:
        output = self._run(arguments, directory, env, target)
        try:
            return [json.loads(line) for line in output.splitlines() if line.strip()]
        except ValueError as error:
            raise AnswerFailed(f"gh api answered in an unknown form: {error}") from error


def _comment(item: Mapping) -> DraftComment:
    return DraftComment(
        id=str(item.get("id") or ""),
        author=item.get("user") or "",
        body=item.get("body") or "",
        created_at=item.get("created_at") or "",
        url=item.get("html_url") or "",
    )


# -- the drafts of a task --------------------------------------------------


def _file(task_dir: Path, draft: str) -> Path:
    if not re.fullmatch(r"p\d+", draft):
        raise FileNotFoundError(draft)
    return task_dir / METADATA_DIR / DRAFTS_DIR / f"{draft}.json"


def save(task_dir: Path, draft: Draft) -> None:
    file = _file(task_dir, draft.id)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(draft.model_dump_json(indent=2))


def load(task_dir: Path, draft: str) -> Draft | None:
    """The draft of that id; None if there is none."""
    try:
        return Draft.model_validate_json(_file(task_dir, draft).read_text())
    except (OSError, ValueError):
        return None


# -- what the agent is told ------------------------------------------------


def approved_message(draft: Draft, edited: bool) -> str:
    how = ", after editing it" if edited else ""
    return f"""\
Draft answer approved: the user approved your draft answer {draft.id} to {draft.url}{how}.
The answer to post:

{draft.answer}

Now:
1. Make the changes to the code that the answer mentions, if it mentions any.
2. If you changed anything, ask the user to review the changes: run
   `bytearmy review --uncommitted` in the background and act on its comments. Once the user
   accepts them, commit the changes and push the task's branch.
3. Run `bytearmy answer send {draft.id}` to post the answer to the conversation on GitHub.
   It sets the task to waiting for the reply.
"""


def discarded_message(draft: Draft) -> str:
    return (
        f"Draft answer discarded: the user discarded your draft answer {draft.id} to "
        f"{draft.url}. They will answer the comment themselves: do not answer it, and make "
        "no changes for it.\n"
    )


# -- the page of an answer pane ----------------------------------------------

STYLE = """\
body { margin: 0; padding: 16px; background: var(--background); color: var(--text);
  font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
h1 { font-size: 16px; margin: 0 0 4px; }
a { color: inherit; }
.muted { color: var(--muted); font-size: 12px; }
.comment { border: 1px solid var(--line); border-radius: 8px; margin: 12px 0;
  background: var(--card); }
.comment.focus { border: 2px solid var(--ring); }
.head { padding: 6px 12px; border-bottom: 1px solid var(--line); font-size: 12px;
  color: var(--muted); }
.head b { color: var(--text); }
.body { padding: 8px 12px; white-space: pre-wrap; overflow-wrap: anywhere; }
pre { margin: 12px 0; padding: 8px 12px; border: 1px solid var(--line); border-radius: 8px;
  overflow-x: auto; font: 12px/1.4 ui-monospace, SFMono-Regular, Menlo, monospace; }
h2 { font-size: 14px; margin: 20px 0 8px; }
textarea { box-sizing: border-box; width: 100%; min-height: 220px; padding: 8px 12px;
  border: 1px solid var(--line); border-radius: 8px; background: var(--background);
  color: var(--text); font: 14px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace;
  resize: vertical; }
.buttons { display: flex; gap: 8px; justify-content: flex-end; margin-top: 8px; }
button { padding: 6px 16px; border-radius: 6px; border: 1px solid var(--line);
  font: inherit; cursor: pointer; background: var(--background); color: var(--text); }
button.approve { background: var(--primary); color: var(--on-primary);
  border-color: var(--primary); }
button.discard { color: var(--destructive); }
button:disabled { opacity: 0.5; cursor: default; }
#result { margin-top: 8px; }
"""

SCRIPT = """\
const answer = document.getElementById('answer')
const result = document.getElementById('result')
async function decide(choice) {
  if (choice === 'approve' && !answer.value.trim()) {
    result.textContent = 'The answer is empty.'
    return
  }
  for (const button of document.querySelectorAll('button')) button.disabled = true
  try {
    const response = await fetch(location.pathname.replace(/\\/$/, '') + '/' + choice, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ answer: answer.value }),
    })
    if (!response.ok) throw new Error((await response.json()).detail || response.status)
    result.textContent = choice === 'approve'
      ? 'Approved: the agent makes the changes and asks you to review them.'
      : 'Discarded: the agent was told that you answer the comment yourself.'
  } catch (error) {
    result.textContent = 'Failed: ' + error.message
    for (const button of document.querySelectorAll('button')) button.disabled = false
  }
}
"""


def page_colors(theme: Theme) -> str:
    extra = {
        "card": "card",
        "ring": "ring",
        "primary": "primary",
        "on-primary": "primary-foreground",
        "destructive": "destructive",
    }
    variables = " ".join(
        f"--{own}: {theme.colors[name]};" for own, name in extra.items() if name in theme.colors
    )
    return colors(theme) + f":root {{ {variables} }}\n"


def render(draft: Draft, theme: Theme) -> str:
    """The conversation, oldest comment first, with the draft below it to edit."""
    comments = "\n".join(
        f'<div class="comment{" focus" * (bool(draft.focus) and comment.id == draft.focus)}">'
        f'<div class="head"><b>{escape(comment.author)}</b> '
        f"{escape(comment.created_at.replace('T', ' ').removesuffix('Z'))}"
        + (f' · <a href="{escape(comment.url)}">on GitHub</a>' if comment.url else "")
        + f'</div><div class="body">{escape(comment.body)}</div></div>'
        for comment in draft.comments
    )
    code = ""
    if draft.path:
        code = f'<p class="muted">{escape(draft.path)}</p>'
    if draft.diff_hunk:
        code += f"<pre>{escape(draft.diff_hunk)}</pre>"
    kind = "a review thread" if draft.thread == "review" else "the conversation"
    return (
        "<!doctype html>\n"
        '<html><head><meta charset="utf-8">'
        f"<title>Draft answer: {escape(draft.title)}</title>"
        f"<style>{page_colors(theme)}{STYLE}</style></head>\n<body>"
        f"<h1>{escape(draft.title)} #{draft.number}</h1>"
        f'<p class="muted">{draft.agent and escape(draft.agent) + " drafts an answer to "}{kind}'
        f' of <a href="{escape(draft.url)}">{escape(draft.owner)}/{escape(draft.repo)}</a></p>\n'
        f"{code}{comments}\n"
        "<h2>Draft answer</h2>"
        f'<textarea id="answer" spellcheck="true">{escape(draft.draft)}</textarea>'
        '<div class="buttons">'
        '<button class="discard" onclick="decide(\'discard\')">Discard</button>'
        '<button class="approve" onclick="decide(\'approve\')">Approve</button>'
        '</div><p id="result" class="muted"></p>\n'
        f"<script>{SCRIPT}</script></body></html>\n"
    )
