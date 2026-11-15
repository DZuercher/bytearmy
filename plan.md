# bytearmy: how to build it

## Context
`design.md` describes a desktop program for macOS and Linux that tracks projects and tasks on disk and gives every task a tiling workspace of tool panes in which Claude agents run. `ui.html` shows the layout: project bar left, backlog bar right, task bar bottom, workspace in the centre. The repo holds only these two files.

This plan answers "how could it be built". On approval I run three throwaway spikes that test the risky parts, report, and stop. Nothing else gets built without a further go.

## Decisions you made
1. Stack: Electron UI + Python core.
2. Quitting stops all processes; restart restores layouts and resumes Claude conversations.
3. UI kit: shadcn/ui + Tailwind, styled like `ui.html`.
4. After approval: three spikes, then stop.

## Architecture

```
pixi run bytearmy
      │
      ▼
Python core (FastAPI, asyncio) ─── starts ───► Electron (thin shell)
  store     .bytearmy/*.json                     └─ React UI
  tasks     mkdir, git clone, delete                  bars, dialogs, palette
  layout    split tree per task                       terminal pane = xterm.js
  ptys      shells, claude, vim                       web pane      = <webview>
  tools     .tools/*.toml                                 │
  services  code serve-web                                │
      ▲  ▲                                                │
      │  └────── HTTP + WebSocket on 127.0.0.1, token ────┘
      │
  `bytearmy` CLI  ◄── run by Claude, or by you, inside any terminal pane
```

1. **The Python core owns state and processes**: projects, tasks, git, terminals (PTYs), layout, tools, the `code serve-web` child. PTYs come from the standard library (`pty`), so there is no native Node module.
2. **Electron is a thin display**: one window, the React UI, embedded web pages, double-Shift detection. Target: under ~300 lines in its main process.
3. **One API on `127.0.0.1`** (random port, token on every request and WebSocket), used by the UI, by the `bytearmy` CLI inside panes, and by pytest. The token is not optional: the API can delete directories and start processes, and web panes show arbitrary sites. TypeScript types are generated from the core's OpenAPI schema, so the two languages cannot drift apart silently.
4. **The file system is the database**: JSON in the `.bytearmy` directories exactly as `design.md` lays them out. pydantic models are the schema; writes are atomic.
5. **Layout is data owned by the core**: a split tree per task. Open, close, move, maximize and highlight are pure Python functions on that tree, so you dragging a pane and an agent calling the CLI run the same code. The UI only draws the tree (react-mosaic).

Startup order: `pixi run bytearmy` starts the core, the core starts Electron, closing the window stops the core and every child process.

## Answers to the two open questions in design.md

### "Open existing programs within panes (how to make this work?)"
A program fits in a pane if it is a terminal program or serves a web UI. Native windows (Finder, Ghostty, desktop VS Code) cannot be moved into another app's window on macOS or Wayland. That gives three pane kinds, declared per tool in `.tools/<tool>.toml`:

| Tool | Kind | What runs |
|---|---|---|
| Terminal | terminal | `$SHELL -l -i` in the task directory |
| Claude | terminal | your `claude` launcher through that same shell, plus flags for session id, name and the bytearmy instruction file; the first one in a task is the master |
| Vim | terminal | `vim <file>` |
| HTML | web | the file, served by the core from the task directory; agent-only |
| Webpage | web | any URL; default is the project's GitHub URL |
| VSCode | web | one shared `code serve-web` process, URL `…/?folder=<repo>` |
| Explorer | external | `open <dir>` (macOS) or `xdg-open <dir>` (Linux); a separate OS window |

Why Electron and not a browser tab: `github.com` and, since VS Code 1.136, `code serve-web` forbid being shown in an iframe. They need a real embedded browser view.

### "How can subagents communicate to the master agent?"
1. The master runs `bytearmy agent spawn --name reviewer --prompt-file brief.md`. The core opens a new Claude pane (every subagent gets its own pane, as `design.md` wants), started with `--name <task>/reviewer` and one line naming its master.
2. Both directions use Claude Code's built-in cross-session messaging (`SendMessage` between local sessions): an idle receiver starts a new turn, a busy one reads the message between tool calls. It is on by default from Claude Code 2.1.224; this machine runs 2.1.312. bytearmy only assigns the names.
3. Fallback if that proves unsuitable: a bytearmy mailbox (`bytearmy msg send`, `bytearmy agent wait`), with the master woken by a `Stop` hook returning `decision: "block"` or by an `asyncRewake` hook. Both are documented.

Claude's own agent-teams split panes only support tmux and iTerm2, so bytearmy has to start the sessions itself. Step 2 is not covered by the three spikes; see proposal 1.

The master changes metadata and panes through the same CLI: `bytearmy task set-status "Needs review" --color amber`, `bytearmy pane open|close|highlight|maximize|move`. Each pane gets its own token in its environment, so the core knows which pane is calling and which task it belongs to.

## State on disk

| File | Holds |
|---|---|
| `<root>/.bytearmy/state.json` | project order, focused project, which bars are collapsed |
| `<root>/<project>/.bytearmy/project.json` | name, description, GitHub URL, backlog order, task-bar order |
| `<root>/<project>/<task>/.bytearmy/task.json` | name, description, status, colour, branch, reminder, dependencies, master pane |
| `<root>/<project>/<task>/.bytearmy/layout.json` | split tree; per pane: tool, arguments, Claude session id |
| `<root>/.tools/<tool>.toml` | kind, command or URL template, parameters, who may call it |

A project or task exists if its directory holds the metadata file. Delete refuses any path outside the root or without that file. Resume on restart: each Claude pane starts with `--session-id <uuid>` stored in `layout.json` and reopens with `claude --resume <uuid>`.

## On approval: three spikes
Cost: about 2 hours of my work, 15 minutes of your clicking, several hundred MB of downloads (Python and Node through pixi into `.pixi/`, Electron through npm). macOS only. No commits.

Files created: `pixi.toml`, `pixi.lock`, `spikes/core.py`, `spikes/cli.py`, `spikes/test_core.py`, `spikes/ui/` (Electron `main.js`, Vite + React renderer).

### Spike A: terminal pane (`pixi run spike-a`)
Builds a Python PTY-to-WebSocket bridge and an Electron window with one xterm.js terminal running your shell, with `BYTEARMY_*` variables and a `PATH` entry for the CLI.

I check with pytest: bytes written by the child process arrive on the WebSocket; a resize message changes what `stty size` reports.

You check:
1. The prompt behaves like your normal zsh.
2. `claude` shows your profile picker, then Claude Code renders correctly (colours, borders, no flicker).
3. Resizing the window reflows Claude.
4. `echo $BYTEARMY_PANE; which bytearmy` print values, so they survived your zsh startup files.
5. `claude --session-id <uuid>`, quit, `claude --resume <uuid>` reopens the same conversation, so flags pass through your launcher.

### Spike B: web panes in a tiling layout (`pixi run spike-b`)
Builds a react-mosaic workspace with three panes: the terminal from A, a `<webview>` on github.com, a `<webview>` on a `code serve-web` instance the core starts. Adds buttons for move and maximize, a mock palette, and double-Shift detection in Electron's main process.

I check: `serve-web` answers on its port with its token.

You check:
1. github.com renders and scrolls.
2. VS Code opens the folder and typing works.
3. Drag the VS Code pane to another edge: your unsaved text is still there, the terminal keeps its content.
4. Maximize and restore: same.
5. Press Shift twice with the cursor inside VS Code: the palette appears above all panes, Esc closes it.

If a check fails, the fallback is Electron's `WebContentsView` placed from the same pane rectangles, with the palette in its own view on top. I report and wait before building that.

This spike starts `code serve-web --accept-server-license-terms`. Approving the plan means you accept Microsoft's VS Code Server license. Say so if I should leave the flag out.

### Spike C: control loop (`pixi run spike-c`)
Builds the `bytearmy` CLI (standard-library Python) with `pane open|close|maximize` and `task set-status`, the RPC endpoint, and the event stream that makes the UI add or remove panes. Passes a short instruction file to Claude with `--append-system-prompt-file`.

I check with pytest: `pane.open` emits the event with the pane spec; a request without the token gets 401.

You check:
1. In the terminal pane, `bytearmy pane open webpage https://github.com` makes a web pane appear.
2. `bytearmy pane maximize <id>` and `bytearmy pane close <id>` work.
3. Start Claude and ask "open this project's GitHub page in a pane": Claude calls the CLI by itself.
4. Ask Claude to repeat the marker words it sees: this shows whether `<root>/CLAUDE.md` and `<root>/<project>/.claude/CLAUDE.md` load automatically.

## Verification
1. After the spikes I report one table: assumption, confirmed or refuted, evidence. Then I stop.
2. No pytest check is reported as covering something until I have seen it fail, by mutating the guarded line in place and restoring it.
3. GUI behaviour is confirmed by you with the checklists above, not by me.

## Where the build differs from design.md (needs your OK)

| design.md says | What gets built | Why |
|---|---|---|
| Terminal uses the system's default terminal | Embedded emulator running your default shell | Ghostty and Terminal.app windows cannot live inside a pane |
| Explorer opens a pane | Finder or the Linux file manager opens as its own window | same reason |
| VSCode opens VS Code | VS Code's web UI in a pane | desktop VS Code is a native window |
| Root `.claude` explains bytearmy to Claude | Same file, passed explicitly at launch | automatic loading of `.claude` above the repo root is undocumented |
| `git clone` in the task directory | Clone lands in `<task>/<repo>/`, `.bytearmy` stays outside it | literal reading; keeps `git status` clean |

## Decisions needed later
1. Where `bytearmy_root` lives. Before M1.
2. What dependencies, reminder date and status do beyond being stored and shown, and whether status is a fixed list or free text. Before M2.
3. Working directory of Claude panes when a task has a clone: the repo (its own `CLAUDE.md` and settings load) or the task directory. Before M3.
4. Launch command for agents the master spawns: your `claude` profile picker would wait for a human each time. Before M5.
5. Which Linux to test on, and how: your machine, a VM, or a CI runner. Before M1 ends.

## Build order after the spikes (each needs your go)

| # | Milestone | What you can use afterwards | Estimate |
|---|---|---|---|
| M1 | Projects | Window with the `ui.html` layout; create, delete, reorder projects; survives restart | 1 day |
| M2 | Tasks | Backlog and task bar, creation dialog, `git clone`, statuses, delete | 1 to 2 days |
| M3 | Workspace | Tiling panes, double-Shift palette, Terminal / Claude / Vim, layout and session restore | 2 days |
| M4 | Web tools | HTML, Webpage, VSCode, Explorer | 1 day |
| M5 | Agent control | CLI, master agent, pane and metadata commands, subagents | 2 days |

Estimates are my build time plus your review. Spike results can shift M3 to M5. Real code goes into `src/bytearmy/` (Python package: models, store, tasks, layout, pty, tools, api, cli) and `ui/` (Electron + React).

## What was checked, and how

On this machine:
1. Node is not on `PATH`; pixi, Rust 1.99 and Python 3 are. Node comes from pixi.
2. `claude` is a zsh function that opens a `gum` profile picker (`aws` / `sub`) before starting Claude Code 2.1.312. Panes must start it through your interactive shell.
3. VS Code is installed but `code` is not on `PATH`. The binary is `/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code`.

In current documentation and source (November 2026):
1. Electron 45: `WebContentsView` always paints above the page. `<webview>` is a DOM element but the Electron docs advise against it. Whether `<webview>` ignores frame-blocking headers is not documented; spike B tests it.
2. `code serve-web` from 1.136 sends `frame-ancestors 'self'`.
3. Claude Code has `--session-id`, `--resume`, `--name`, `--append-system-prompt-file`, `--settings`. `CLAUDE.md` loads from every directory above the working directory; `.claude/settings.json` and skills above the repo root are not documented to load.
4. react-mosaic 7.2.1 keeps a tile mounted when it moves; it has no built-in maximize, so maximize is a flag beside the tree applied with CSS.
5. xterm.js is `@xterm/xterm` 6; it renders with DOM or WebGL only.

## Proposals (not part of this plan; pick by number)
1. Spike D: two Claude panes exchange a message with built-in `SendMessage`. Decides whether bytearmy needs its own mailbox. 20 minutes.
2. Task-tab status driven automatically by Claude hooks (working / needs input / done). Half a day, inside M5.
3. `git worktree` per task instead of a full clone: faster task creation, less disk. 2 hours; changes a `design.md` rule.
4. Read Wave Terminal's source before M3: as far as I remember it is an open-source Electron UI with a separate backend process and terminal and web blocks. Not verified. 1 hour.
5. Packaged `.dmg` and AppImage instead of `pixi run bytearmy`. 1 to 2 days, after M5.
