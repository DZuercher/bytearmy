# BYTEARMY Designdoc

bytearmy should help the user to coordinate with several agents running at the same time, working on multiple tasks in parallel in multiple projects.
It should also enable human-to-agent and agent-to-human communication more effectively and serve as a task tracking tool.

## Task tracking

### Projects
The user should be able to create `Projects`. 
When creating a new project a project name must be provided
Two projects cannot have the same name.
Optionally, a github origin URL can be provided.
Optionally, a project description can be provided.
The project is created at `<bytearmy_root>/<project_slug>`. The slug is derived from the name (`Project 1` becomes `project-1`), the name itself can be any text.
The name, description and url is tracked in the projects metadata (.bytearmy directory).

#### Project bar
The project should be visible in the project bar on the left side.
The bar should be collapsable
The project bar contains a `+` button which allows to add new projects, opening a creation dialogue.
Left clicking on a project should focus on the project (task bar and backlog task bar for this project are shown).
When right-clicking on a project the projects data should be shown as well as a button allowing to delete the project.
When deleting a project show a warning informing the user that all the data associated with the project will be deleted and ask for confirmation.
It should be possible to drag and drop projects to change their order, the order should be persistet when restarting the program.

### Backlog tasks
The user should be able to create new backlog tasks.
When creating a task a name must be provided.
Two tasks of the same project cannot have the same name.
Optionally a description of the task can be provided and a branch name (only show this option if a github url was provided for the project). 
Optionally, dependencies on other already existing tasks and a reminder date can be provided.
From its reminder date on a task shows a reminder marker in its bar and in its data.
The task is created at `<bytearmy_root>/<project_slug>/<task_slug>` where the project is the currently focused project in the project bar. The slug is derived from the name like for projects (`Fix login` becomes `fix-login`).
If a Github URL is provided for the current project the repository is cloned into `<bytearmy_root>/<project_slug>/<task_slug>`.
The repository is downloaded only once per project: the first clone creates a bare mirror of its branches in the projects `.bytearmy` directory, every clone first fetches into the mirror and then clones from it. A clone from the mirror shares its files by hard links, so it needs no download and hardly any disk, yet it is a repository of its own whose `origin` is the Github URL.
The clone lands in `<bytearmy_root>/<project_slug>/<task_slug>/<repo_name>`, so the tasks `.bytearmy` directory stays outside of the repository.
If a branch name was provided the branch is checked out after the clone if it exists on the remote, otherwise it is created from the default branch, or from the branch chosen under "Branch off from". That list holds the remote's branches, read from the project's mirror after a fetch; the default branch is preselected. A base branch without a branch name is checked out itself. The diff pane of such a task compares with its base branch instead of the default branch.
If the clone fails, the task shows a warning marker and its data says why. Its right-click menu then offers to clone again, with the projects Github URL and the tasks branch.
The task then becomes visible in the backlog task bar. The status of the task should be set to `Backlog`.
The status, name, description, branch, reminder date, dependencies of a task is tracked in its metadata (.bytearmy directory).

#### Backlog task bar

The backlog task bar contains a `+` button which allows to add new backlog tasks, opening a creation dialogue.
Left clicking on a task moves it from the backlog task bar into the task bar. It should be the last task in the task bar then. Its status should be set to `New`.
A task cannot be moved into the task bar while a task it depends on is still in the backlog. It shows which tasks it waits for.
When right-clicking on a task the tasks data should be shown as well as a button allowing to delete the task.
When deleting a task show a warning informing the user that all the data associated with the task will be deleted and ask for confirmation.
It should be possible to drag and drop tasks to change their order, the order should be persistet when restarting the program.

#### Focus mode

A small `Focus mode` tab sits above the backlog task bar. Clicking it turns focus mode on or off. In focus mode the project bar, the task bar and the backlog task bar are collapsed and the `Inbox` tab does not show the number of unread messages, so the user can work on one task without distraction. Turning focus mode off brings back the bars as they were. Expanding a bar in focus mode ends it, with only that bar open. Focus mode is kept with the state of the bars.

### Tasks
Every task has a status. When a task is placed from the backlog into the task bar it is set to `New`.
The status is shown as a small tag below the task name in the task bar.
When a task is clicked on in the task bar the workspace of the task is shown.
The agents working in a task can change its state, its status, its description and its reminder with the `bytearmy` command line tool; its name, branch and dependencies stay as the user set them.
The status is free text; only `Backlog` is not allowed, as a started task cannot go back.
The state alone colors the task in the bars, so that the colors mean the same in every task:
- `backlog` (no color): the task is in the backlog. `new` (no color): the task was started, nobody has set a state yet.
- `working` (blue): an agent works on the task itself, nothing blocks it.
- `needs_input` (amber): the user has to do something.
- `waiting` (red): an agent waits for an event outside bytearmy, such as a CI run.
- `done` (green): an agent thinks the task is done, nothing more needs to be done.
- `merged` (purple): the task's pull request is merged; only in projects with a GitHub URL.
- `manual` (black): set only by the user, in the edit dialog.
Agents set `working`, `needs_input`, `waiting`, `done` and `merged`; the user can set every state of a started task.

#### Scratchpad
Every project has a task called `Scratchpad`, with the status `Explore`, for the user to explore the project and ask general questions. It is created with the project (projects made before it existed get theirs at the next start) and, if the project has a GitHub URL, holds a clone of the default branch. It is always shown at the very top of the task bar, apart from the other tasks: it cannot be dragged, and the others cannot be dragged above it. It cannot be deleted; otherwise it is a task like any other.
When right-clicking on a task the tasks data should be shown as well as a button allowing to delete the task.
When deleting a task show a warning informing the user that all the data associated with the task will be deleted and ask for confirmation.
It should be possible to drag and drop tasks to change their order, the order should be persistet when restarting the program.

## Frontend
A visual representation of the frontend can be found in ui.html.

## Workspace
The workspace is a tiling window manager. 
The user can double tap the shift button to open a command dialogue (like dmenu on linux) from which a tool can be selected.
When choosing a tool a new pane is opened in the workspace showing the tool specific pane.
The current layout of a workspace is stored in the tasks metadata.
A pane closes when its program ends without an error. If the program fails the pane stays and shows the exit code.
When bytearmy quits, the programs in all panes are stopped. When it starts again the panes of all tasks are restored with their layout and their programs are started again. A Claude pane continues its earlier conversation.
Each task has one master agent, which is the agent that was created first in the task. If the master's pane is closed, the next Claude pane that is opened becomes the master.
Agents create, close, hide, show, highlight, maximize and rearrange panes with the `bytearmy` command line tool, which is available in every terminal pane:
- `bytearmy pane list|open|close|hide|show|maximize|restore|highlight|unhighlight|move`, e.g. `bytearmy pane open webpage <url>` or `bytearmy pane maximize <id>`
- `bytearmy tool list`
- `bytearmy task show|set-status|working|needs-input|waiting|done|merged|set-description|set-reminder`, e.g. `bytearmy task needs-input "Needs review"`; a state command without a status sets the state's name as the status
- `bytearmy agent list|spawn`

The command works only on the task of the pane it runs in: bytearmy gives the program of every pane a key of its own. Every pane of the task may run every command; that the master leads the others is said only in its instructions.
A pane opened with the command appears next to the pane that called it, and its program starts at once, also when the task is not shown.
A highlighted pane gets an amber frame. The frame goes away when the user clicks into the pane, or when an agent ends the highlight.

A pane can be hidden: it has no place on the workspace, but its program keeps running.
The title bar of every pane has a button next to the maximize button that hides it. An agent hides a pane with `bytearmy pane hide <id>`, shows it again with `bytearmy pane show <id>`, and opens a pane hidden with `bytearmy pane open <tool> --hidden`.
The hidden panes of a task are listed below the task in the tasks bar. The list is collapsed by default; a small arrow on the task unfolds it, and whether it is unfolded survives a restart. Clicking a hidden pane in the list shows it on the workspace, next to the pane last used in its task, and shows that task's workspace.
Hiding the maximized pane ends the maximize; a hidden pane cannot be maximized, nor moved, nor have a pane moved next to it. Highlighting a hidden pane shows it.
A web pane that was hidden loads its page anew when it is shown again; a terminal pane shows the recent output of its program.
Whatever an agent changes shows in the window at once.

Subagents are always created in their own hidden pane: `bytearmy agent spawn <role> "<prompt>"` starts a new agent in a hidden pane, in the same directory, with the prompt as its first message. The agent that spawned it closes its pane once it needs nothing more from it.
Every agent is a Claude session with its own name (`claude --name <name>`), `<project>-<task>-<role>`. The master has the role `master`, a Claude pane the user opens while there is a master gets `agent-2`, `agent-3` and so on, and a spawned agent gets the role its spawner chose (lower-case letters, digits and dashes, up to 32 characters). No two agents of a task have the same role.
Every agent starts with the instructions in `<bytearmy_root>/.claude/bytearmy.md`, followed by its name, its pane, who the master is and who spawned it. bytearmy writes these anew at every start, so a restarted agent learns who the master is now.
The agents talk to each other with the cross-session messaging that is built into Claude Code (`SendMessage`). An idle agent starts a new turn when a message arrives, a busy agent reads it between two tool calls.
The names are visible to every Claude session of the user on the machine, so they must be unique across projects and tasks. Two roots that run at the same time and hold a project and task of the same names give their agents the same names.

#### Event listeners
An agent that waits for something outside bytearmy, such as a CI run, hands it to an event listener instead of watching it itself: `bytearmy listen "<event>" "<command>"` runs a command that ends when the event happens (e.g. `gh pr checks 12 --watch`) in a hidden pane of the tool `event_listener`. When the command ends, bytearmy types a message that starts with `Event:`, with the exit code and the last lines the command printed, into the agent that opened the listener, or into the master if that agent is gone, logs it, and closes the pane. Closing the pane earlier tells nobody.
A listener's command that ends within 3 seconds of its start most likely failed, e.g. for a missing permission or login, and listens for nothing. So `bytearmy listen` (and `bytearmy pane open` of a tool that notifies) watches the command that long: if it ends by then, the call itself reports it, with the exit code and the last lines, the pane closes, and no message follows. With exit code 0 the call says the event has already happened; with any other code the call fails, so the agent fixes the command at once instead of waiting for nothing.
An event listener belongs to bytearmy, not to the agent's Claude process, so it outlives a quit and a reboot: when bytearmy quits it stops the listener without a message, and when bytearmy starts again it runs the listener's command again, like the program of every other pane. The command must therefore be safe to run more than once. A background task an agent starts itself (Bash with `run_in_background`, Monitor) dies with bytearmy, is not started again by `claude --resume`, and the agent is never told; the agent instructions therefore tell agents to use `bytearmy listen` for every wait that may outlive bytearmy.
bytearmy types a message into an agent only once the agent can take it: a program that started less than 2 seconds ago, or printed something in the last second, gets it later, at the latest 30 seconds after it started. So a listener that ends right after a restart does not lose its message while the resumed Claude session is still loading.
The instructions bytearmy writes at every start list the event listeners whose message goes to the agent (for the master also those whose opener is gone), so a resumed agent learns which of its listeners run again.

## Tools
Calling a tool opens a new pane on the workspace.
The tools are found in .tools. Ideally these just open already existing programs but within the panes in bytearmy.
Every tool is one file `<bytearmy_root>/.tools/<tool>.toml` holding its name, its kind, its command or page and its parameters. A project can have tool files of its own in `<project>/.tools`: a file there replaces the one of the same name in that project, e.g. to start its agents with another `spawn` line. bytearmy writes the files of the premade tools if they are missing. When a newer bytearmy brings a new version of a premade file, it replaces the files nobody edited; a file the user changed is never overwritten. The same holds for `<bytearmy_root>/.claude/bytearmy.md`.
A tool file also says who may call the tool (the user, agents or both), what a parameter is set to if nothing is entered (e.g. the projects GitHub page), which background program the tool needs (e.g. the VS Code server of VSCode), whether its program is an agent, and how it is started when another agent spawns it.
A program can run inside a pane if it is a terminal program or if it serves a web UI. Windows of native programs (Finder, Ghostty, the VS Code desktop app) cannot be placed inside a pane.
Every tool therefore has one of five kinds:
- terminal: the program runs in a terminal emulator inside the pane (xterm.js connected to a pseudo-terminal).
- web: the pane is an embedded browser view (Electron `<webview>`) showing a URL or a file.
- external: the program opens in its own window outside of bytearmy, no pane is created.
- log: adds its parameter `entry` to the task's log, no pane is created.
- diff: the pane shows the changes of the task's clone for the user to review (see Reviews below).

Web panes:
- A web pane shows only http and https pages. An address without `http://` or `https://` is opened with `https://`.
- The title bar of a web pane has buttons to go back, go forward and load the page again.
- A link or script in a web pane that wants a new window opens the page as a new Webpage pane next to it instead.
- All web panes share one browser session that is kept in `<bytearmy_root>/.bytearmy/electron`, so a login survives a restart. Every root has its own.
- After a restart a web pane shows the page it was opened with, not the last page that was visited in it.

The user calls a tool from the command dialogue, an agent calls it with the `bytearmy` command line tool.
Tools can be added by the user or agents but the following tools should come premade with bytearmy.
- Terminal [terminal. Opens the users default shell in the embedded terminal emulator. The systems terminal application itself cannot be embedded.]
- Claude [terminal. Same as Terminal but directly executes `claude` to spawn a claude agent. The first Claude pane of a task becomes its master agent. Claude starts with its name (`--name`) and bytearmy's instructions (`--append-system-prompt-file`). The command runs through the users interactive shell, so a shell function or alias named `claude` works. It starts in the clone of the repository if the task has one, otherwise in the tasks working directory. Agents that other agents spawn are started with the `spawn` line of the tool file; as `claude` may first ask which account to use, the user can put a command there that does not ask.]
- Vim [terminal. Opens the vim editor. Needs to be pointed to a specific file in the workspace.]
- Lazygit [terminal. Runs `lazygit` in the repository in the task, if there is one, otherwise in the tasks working directory.]
- HTML [web. Renders a static html file in the workspace. Can only be invoked by the agent, so the command dialogue does not list it. The file must lie inside the tasks working directory; bytearmy serves it to the pane under a random key, not under the token of its API. When the file changes the pane loads it again.]
- Webpage [web. Shows a specific webpage. Eg github page associaated with the projects github url. The command dialogue offers the projects GitHub page; an address can be typed instead.]
- VSCode [web. Opens the web UI of vscode (`code serve-web`) in the repository in the task, if there is one, otherwise in the tasks working directory. The vscode desktop app cannot be embedded. One VS Code server serves all VSCode panes; it starts with the first VSCode pane and ends when bytearmy quits. It is started with `--accept-server-license-terms`, which stands in the tool file.]
- Explorer [external. Opens systems file explorer in the tasks working directory, in its own window. If it fails within two seconds, the command dialogue shows why.]
- PyCharm [external. Opens the repository in the task in PyCharm, if there is one, otherwise the tasks working directory, in its own window (`open -a PyCharm .` on macOS, `pycharm .` on Linux). If PyCharm already runs, the project opens as a new window of it. The PyCharm desktop app cannot be embedded.]
- Log [log. Adds an entry to the tasks log in `<task>/.bytearmy/log.jsonl`. Each entry holds the current time in UTC (ISO 8601), its author (the name of the agent, or `user`) and the text. The user calls it from the command dialogue, an agent with `bytearmy log "<text>"`.]
- Show Log [web. Shows the tasks log as an HTML page, oldest entry first. bytearmy renders the page from `log.jsonl` and serves it under the same random key as the files of the HTML tool. When an entry is added the pane loads it again.]
- Review Changes [diff. Shows the changes of the repository in the task, if there is one, otherwise of the tasks working directory, for the user to review and comment on. See Reviews below.]

Reviews:
- A diff pane shows [diffle](https://diffle.app), which bytearmy starts for that pane on a free port (`diffle <revs> -C <repo> -p <port> --no-open`) and stops when the pane closes. diffle comes with bytearmy's pixi environment; without it on PATH the pane says so. It compares the working tree, untracked files included, with where the task's branch left the default branch (origin's HEAD, else `origin/main`, `origin/master`, `main`, `master`), or with HEAD only ("uncommitted", `diffle working`). The index is never touched.
- The user comments in diffle, on a line, a block or a whole file. diffle keeps the comments in `<git-dir>/diffle/`, so they survive a restart.
- Closing the pane, or its "Finish review" button, finishes the review: bytearmy reads diffle's open threads as one prompt (`/api/threads/export`), puts "Review comments from the user on the changes against <base>:" in front of it, resolves the threads so the next review starts without them, and stops diffle. The log gets an entry.
- A review the user opens goes to the master: bytearmy types the message into the master's terminal as a bracketed paste followed by Enter, so Claude takes it as a message of the user (a busy Claude queues it). Without a running master the pane still closes; the comments stay open in diffle and show again in the next review.
- An agent asks for a review with `bytearmy review [--uncommitted] [--note "<text>"]`, best run in the background. It opens a highlighted diff pane beside the agent's pane, with the note in its title, and waits; when the user finishes the review, the command prints the message and ends, and the master gets nothing.

What a program without a pane (an external tool, the VS Code server) prints goes to `<bytearmy_root>/.bytearmy/logs/<tool>.log`.


## Inbox
The inbox gathers messages for the user from sources outside bytearmy. GitHub is the first source; email, Slack and Webex are to follow.
A small `Inbox` tab sits just above the project bar. It shows the number of unread messages. Clicking it shows the inbox in place of the workspace; choosing a project, a task or a pane shows the workspace again. Which of the two is shown is kept with the state of the bars.
The inbox lists the messages of all sources, newest first: title, where it comes from (for GitHub the repository), why it reached the user and when. Unread messages are marked. Selecting a message shows its page beside the list, in a browser view that shares the session of the web panes, and marks it read. A message can be marked read or unread, or dismissed, which hides it until it sees new activity.
A thread in the core asks every source for new messages once a minute; the refresh button asks at once. What is new shows in the window at once. A source that cannot be asked shows why above the list.
The messages are kept in `<bytearmy_root>/.bytearmy/inbox.json`.

### GitHub
The messages from GitHub are the users notifications (what github.com/notifications shows). bytearmy asks for them with `gh`, as the user logged in with `gh auth login`, or as the account `[github] user` of the config names; it keeps no token of its own. Marking a message read marks the notification read on GitHub; dismissing it marks it done. Marking unread stays in bytearmy, as GitHub cannot. A notification read on GitHub turns read in bytearmy.

## Orchestrator
Above the masters of the tasks there is one more agent, the orchestrator. There is exactly one per bytearmy; it belongs to no project.
A small `Orchestrator` tab sits above the `Inbox` tab. Clicking it shows the orchestrator's terminal in place of the workspace; choosing a project, a task or a pane shows the workspace again. Which of the three is shown is kept with the state of the bars. The terminal stays mounted once shown, so it keeps its content. When the orchestrator does not run, the tab shows a red dot and the view a button that starts it again.
The orchestrator is a Claude session named `orchestrator`, started with the Claude tool of the root in the root directory, so it can read every project. It starts with bytearmy and is ended when bytearmy quits; after a restart it resumes its conversation. If it ends with an error, it stays ended until the user starts it again. Its session and terminal size are kept in `<bytearmy_root>/.bytearmy/orchestrator.json`.
It starts with the instructions in `<bytearmy_root>/.claude/orchestrator.md`, followed by its name. bytearmy writes the file if it is missing and replaces it when nobody edited it, like `bytearmy.md`.
Its `bytearmy` command has a key of its own, which reaches no task's commands; the commands of the tasks' panes cannot reach its commands:
- `bytearmy project list|create|delete`
- `bytearmy tasks list|create|start|delete`. `tasks start <project> <task> --prompt "<text>"` moves the task into the task bar and starts its master, on the workspace, with the prompt as its first message. A task that already has a master gets no second one.
- `bytearmy inbox list|read|unread|dismiss`

The orchestrator talks to the masters with `SendMessage`, by their names (`<project>-<task>-master`); the masters answer the same way and are told in their instructions that `orchestrator` stands above them. A master the orchestrator started is told so, and that it should report when it is done or stuck.
When the inbox finds messages that are new or saw new activity, bytearmy types a note naming them into the orchestrator's terminal, as if the user had typed it. It waits until the user has typed nothing into that terminal for 30 seconds. What arrived while bytearmy did not run is not announced. The orchestrator's instructions tell it to turn messages that ask for work into tasks, start them, and delete a task once its master reported it done and its work is saved.

## Configuration
The settings of bytearmy are TOML files named `config.toml`:
- `<bytearmy_root>/.bytearmy/config.toml` holds the settings of all projects and tasks. It is always there: bytearmy writes it with the defaults, each explained in a comment, when it is missing, and never overwrites it.
- `<project>/.bytearmy/config.toml` and `<project>/<task>/.bytearmy/config.toml` are optional and set any setting again for one project or task. A task's file overrides its project's, which overrides the root's. Tables merge key by key; other values (strings, numbers, lists) are replaced.
- What belongs to no project counts only in the root's file: `color_scheme`, `[orchestrator]` and `[inbox]`. Set in a lower file, it is ignored.
- The files are read again whenever a setting is needed, so an edit counts at once; an agent that runs keeps how it was started. A file that cannot be read, or holds a setting bytearmy does not know, is ignored as a whole; a banner at the top of the window says why, for the files that hold in the focused task.

The settings:
- `color_scheme`: the color scheme of the window, a file in `<bytearmy_root>/.bytearmy/themes`.
- `[orchestrator]`: `autostart` (start it with bytearmy, otherwise when its tab is first shown); `command` and `resume`, which replace those of the Claude tool; `allow`, the `bytearmy` commands it may call (`project.list|create|delete`, `tasks.list|create|start|delete`, `inbox.list|read|dismiss`), as patterns like `tasks.*`; `allow_task_creation` and `allow_task_deletion`, which, when false, forbid `tasks.create` and `tasks.delete` whatever `allow` says; Claude's own permissions `permission_mode`, `allowed_tools` and `disallowed_tools`; `announce_inbox` and `quiet_seconds` for the notes about new messages.
- `[master]` and `[agents]`: the same for the master of a task and for every other agent of it, plus `spawn`. Their `allow` names the commands of the panes: `pane.list|open|close|maximize|hide|highlight|move`, `tool.list`, `task.show|set-status|working|needs-input|waiting|done|merged|set-description|set-reminder`, `agent.list|spawn`, `review`, `log`. A pane without an agent is the user's, and may call all. `[master]` also has `allow_pr_creation` and `allow_pr_merge`: when false, `gh pr create` or `gh pr merge` is added to the tools Claude may not use, and the master's instructions tell it to leave that to the user.
- `[tools.<id>]`: `enabled = false` takes a tool out of the command dialogue and away from the agents; `name`, `callers`, `command`, `resume`, `spawn`, `url` and `directory` replace what its tool file says.
- `[inbox]`: `enabled`, `poll_seconds` (at least 60), and in `[inbox.github]` `enabled`, `reasons` and `repositories`, which keep only the notifications for these reasons and of these repositories.
- `[github]`: `user`, `host`, `name` and `email`. With a user, `gh` and `git` act as that account of `gh auth login` instead of gh's active one: bytearmy asks `gh auth token --user` for its token and gives it, as `GH_TOKEN` with gh as git's credential helper, to the inbox, the clones and the programs in the panes. If gh does not know the account, the active one is used and the banner says so. `name` and `email` become git's `user.name` and `user.email` (as `GIT_CONFIG_*` entries in the same environment), so commits in the clones and panes carry them, with or without a user; empty keeps git's own.

A call the config does not allow is refused, and the agent's instructions name the commands and actions it may not call or do. Claude's permissions reach Claude through `{permissions}` in the command of the Claude tool or of the config.

## Directory structure
Each task is assigned to a project and has its own working directory. 
`bytearmy_root` is `~/bytearmy` by default. It can be changed with the `--root` option or the `BYTEARMY_ROOT` environment variable.
If the root does not exist, bytearmy asks whether to create it and does not start without a yes, so a mistyped root does not start an empty bytearmy.
Only one bytearmy runs on a root at a time: a second one refuses to start and names the process that has the root. The claim on the root ends with that process, also when it crashes.

bytearmy_root
|- .tools [contains tools]
|- .claude [contains bytearmy.md, the explanation for claude about how bytearmy works and how claude can interact with it. It is passed to every Claude pane when it starts. orchestrator.md is that of the orchestrator]
|- .bytearmy [contains config.toml, the settings of all projects and tasks, global metadata, the color schemes, the messages of the inbox, the session of the orchestrator, the logs of programs without a pane, the browser session of the web panes, the `bytearmy` command of the panes and the lock that keeps a second bytearmy off the root]
|-- project-1
    |- .bytearmy [contains bytearmy project level metadata, like name and Github URL, the mirror of the projects repository the tasks clone from, and optionally the project's config.toml]
    |- .claude [contains project specific claude configurations like CLAUDE.md etc. Claude loads this CLAUDE.md by itself in every task of the project]
    |- task-1
        |- .bytearmy [contains bytearmy metadata, like current state of the UI, the instructions each agent of the task started with, and optionally the task's config.toml]
        |- <repo_name> [clone of the projects Github URL, if there is one]
    |- task-2
        |- .bytearmy [contains bytearmy metadata, like current state of the UI]
    |- task-3
        |- .bytearmy [contains bytearmy metadata, like current state of the UI]
|-- project-2
    |- .bytearmy [contains bytearmy project level metadata, like name and Github URL]
    |- task-1
        |- .bytearmy [contains bytearmy metadata, like current state of the UI]
|-- project-3
    |- .bytearmy [contains bytearmy project level metadata, like name and Github URL]
    |- task-1
        |- .bytearmy [contains bytearmy metadata, like current state of the UI]
    |- task-2
        |- .bytearmy [contains bytearmy metadata, like current state of the UI]
