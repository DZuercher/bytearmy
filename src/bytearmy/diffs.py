"""The changes of a task's clone, as the diff pane shows them, and the review the user
writes about them, as the master agent reads it."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from bytearmy.models import Diff, DiffFile, DiffLine, DiffMode, Hunk, ReviewComment

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"  # what git compares a new repo with
DEFAULT_BRANCHES = ("origin/main", "origin/master", "main", "master")
MAX_FILE_LINES = 5000  # a file with a longer diff is listed without its lines
HUNK_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")
REVIEW_HEADING = "Review comments from the user"
# The same output whatever the user configured: no color, no external tool, a/ and b/ prefixes.
DIFF_FLAGS = ("--no-color", "--no-ext-diff", "--src-prefix=a/", "--dst-prefix=b/")


class GitFailed(RuntimeError):
    """git could not tell the changes; the message says why."""


def _git(repo: Path, *arguments: str, ok: tuple[int, ...] = (0,)) -> str:
    done = subprocess.run(
        ["git", "-c", "core.quotePath=false", *arguments],
        check=False,
        cwd=repo,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        errors="replace",
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"},
    )
    if done.returncode not in ok:
        raise GitFailed(done.stderr.strip() or f"git {arguments[0]} failed")
    return done.stdout


def _verify(repo: Path, ref: str) -> str | None:
    """The commit a ref names; None if there is none."""
    found = _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", ok=(0, 1))
    return found.strip() or None


def default_branch(repo: Path) -> str | None:
    """The branch the task's work goes into: origin's HEAD, else a usual name."""
    head = _git(repo, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD", ok=(0, 1))
    for ref in filter(None, [head.strip(), *DEFAULT_BRANCHES]):
        if _verify(repo, ref):
            return ref
    return None


def base(repo: Path, mode: DiffMode) -> tuple[str, str]:
    """The commit the working tree is compared with, and a label for it.

    branch: where HEAD left the default branch. uncommitted: HEAD.
    """
    head = _verify(repo, "HEAD")
    if head is None:
        return EMPTY_TREE, "an empty repository"
    if mode == "branch":
        branch = default_branch(repo)
        if branch is not None:
            fork = _git(repo, "merge-base", "HEAD", branch, ok=(0, 1)).strip()
            if fork:
                return fork, branch
    return head, "HEAD"


def diff(repo: Path, mode: DiffMode) -> Diff:
    """The changes of the working tree, untracked files included, against `base`.

    The index is never touched.
    """
    commit, label = base(repo, mode)
    text = _git(repo, "diff", *DIFF_FLAGS, "-M", commit, "--")
    files = parse(text)
    untracked = _git(repo, "ls-files", "--others", "--exclude-standard", "-z").split("\0")
    for path in filter(None, untracked):
        added = _git(repo, "diff", *DIFF_FLAGS, "--no-index", "--", os.devnull, path, ok=(0, 1))
        files.extend(parse(added))
    files.sort(key=lambda file: file.path)
    return Diff(mode=mode, base=label, commit=commit, files=files)


def _path(header: str) -> str:
    """The path in a `--- a/x` or `+++ b/x` line; empty for /dev/null."""
    path = header[4:].rstrip("\t")
    if path.startswith('"') and path.endswith('"'):
        path = path[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if path == "/dev/null":
        return ""
    return path[2:] if path[:2] in ("a/", "b/") else path


def _git_line_paths(line: str) -> tuple[str, str]:
    """The paths in `diff --git a/x b/x`, if both are the same (no rename)."""
    rest = line.removeprefix("diff --git ")
    half = (len(rest) - 1) // 2
    old, new = rest[:half], rest[half + 1 :]
    if old[2:] == new[2:]:
        return old[2:], new[2:]
    return "", ""


def parse(text: str) -> list[DiffFile]:
    """The files of a unified diff as `git diff` writes it."""
    files: list[DiffFile] = []
    file: DiffFile | None = None
    old_line = new_line = 0
    lines = 0
    in_hunk = False
    for line in text.split("\n"):
        if line.startswith("diff --git "):
            old, new = _git_line_paths(line)
            file = DiffFile(path=new, old_path=old, status="modified")
            files.append(file)
            lines = 0
            in_hunk = False
            continue
        if file is None:
            continue
        if in_hunk and line[:1] in (" ", "+", "-"):
            kind = {" ": "context", "+": "add", "-": "delete"}[line[0]]
            entry = DiffLine(
                kind=kind,
                old=old_line if kind != "add" else None,
                new=new_line if kind != "delete" else None,
                text=line[1:],
            )
            old_line += kind != "add"
            new_line += kind != "delete"
            file.additions += kind == "add"
            file.deletions += kind == "delete"
            lines += 1
            if lines > MAX_FILE_LINES:
                file.truncated = True
                file.hunks = []
            elif not file.truncated:
                file.hunks[-1].lines.append(entry)
            continue
        if match := HUNK_RE.match(line):
            in_hunk = True
            old_line, new_line = int(match[1]), int(match[2])
            if not file.truncated:
                file.hunks.append(Hunk(header=line, lines=[]))
            continue
        if line.startswith("\\"):  # "\ No newline at end of file"
            continue
        in_hunk = False
        if line.startswith("new file mode"):
            file.status = "added"
        elif line.startswith("deleted file mode"):
            file.status = "deleted"
        elif line.startswith("rename from "):
            file.old_path, file.status = line.removeprefix("rename from "), "renamed"
        elif line.startswith("rename to "):
            file.path = line.removeprefix("rename to ")
        elif line.startswith("Binary files "):
            file.binary = True
        elif line.startswith("--- "):
            file.old_path = _path(line) or file.old_path
        elif line.startswith("+++ "):
            file.path = _path(line) or file.path
    for found in files:
        if found.status == "added":
            found.old_path = ""
        elif found.status == "deleted":
            found.path = found.path or found.old_path
        if found.status != "renamed" and found.old_path == found.path:
            found.old_path = ""
    return files


def format_review(label: str, comments: list[ReviewComment], summary: str) -> str:
    """The review as a message to an agent: what the user said, line by line."""
    parts = [f"{REVIEW_HEADING} on the changes against {label}:"]
    if summary.strip():
        parts.append(summary.strip())
    for comment in comments:
        if comment.start == 0:
            where = f"{comment.path} (whole file)"
        else:
            lines = (
                f"{comment.start}"
                if comment.end <= comment.start
                else (f"{comment.start}-{comment.end}")
            )
            side = " (old version)" if comment.side == "old" else ""
            where = f"{comment.path}:{lines}{side}"
        quote = "\n".join(f"> {line}" for line in comment.quote.splitlines())
        parts.append("\n".join(filter(None, [f"## {where}", quote, comment.body.strip()])))
    if not comments and not summary.strip():
        parts.append("The user finished the review without comments.")
    return "\n\n".join(parts) + "\n"
