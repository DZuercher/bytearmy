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
If a Github URL is provided for the current project `git clone <github url>` in `<bytearmy_root>/<project_slug>/<task_slug>`.
The clone lands in `<bytearmy_root>/<project_slug>/<task_slug>/<repo_name>`, so the tasks `.bytearmy` directory stays outside of the repository.
If a branch name was provided the branch is checked out after the clone if it exists on the remote, otherwise it is created from the default branch.
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

### Tasks
Every task has a status. When a task is placed from the backlog into the task bar it is set to `New`.
The status is shown as a small tag below the task name in the task bar.
When a task is clicked on in the task bar the workspace of the task is shown.
The agents working in a task can change its status, its color, its description and its reminder with the `bytearmy` command line tool; its name, branch and dependencies stay as the user set them.
The status is free text; only `Backlog` is not allowed, as a started task cannot go back.
The color (red, amber, green, blue, purple or none) colors the task in the bars, to get the user's attention.
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
- `bytearmy task show|set-status|set-color|set-description|set-reminder`, e.g. `bytearmy task set-status "Needs review" --color amber`
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

## Tools
Calling a tool opens a new pane on the workspace.
The tools are found in .tools. Ideally these just open already existing programs but within the panes in bytearmy.
Every tool is one file `<bytearmy_root>/.tools/<tool>.toml` holding its name, its kind, its command or page and its parameters. bytearmy writes the files of the premade tools if they are missing. When a newer bytearmy brings a new version of a premade file, it replaces the files nobody edited; a file the user changed is never overwritten. The same holds for `<bytearmy_root>/.claude/bytearmy.md`.
A tool file also says who may call the tool (the user, agents or both), what a parameter is set to if nothing is entered (e.g. the projects GitHub page), which background program the tool needs (e.g. the VS Code server of the IDE), whether its program is an agent, and how it is started when another agent spawns it.
A program can run inside a pane if it is a terminal program or if it serves a web UI. Windows of native programs (Finder, Ghostty, the VS Code desktop app) cannot be placed inside a pane.
Every tool therefore has one of four kinds:
- terminal: the program runs in a terminal emulator inside the pane (xterm.js connected to a pseudo-terminal).
- web: the pane is an embedded browser view (Electron `<webview>`) showing a URL or a file.
- external: the program opens in its own window outside of bytearmy, no pane is created.
- log: adds its parameter `entry` to the task's log, no pane is created.

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
- HTML [web. Renders a static html file in the workspace. Can only be invoked by the agent, so the command dialogue does not list it. The file must lie inside the tasks working directory; bytearmy serves it to the pane under a random key, not under the token of its API. When the file changes the pane loads it again.]
- Webpage [web. Shows a specific webpage. Eg github page associaated with the projects github url. The command dialogue offers the projects GitHub page; an address can be typed instead.]
- IDE [web. Opens the web UI of vscode (`code serve-web`) in the repository in the task, if there is one, otherwise in the tasks working directory. The vscode desktop app cannot be embedded. One VS Code server serves all IDE panes; it starts with the first IDE pane and ends when bytearmy quits. It is started with `--accept-server-license-terms`, which stands in the tool file.]
- Explorer [external. Opens systems file explorer in the tasks working directory, in its own window. If it fails within two seconds, the command dialogue shows why.]
- Log [log. Adds an entry to the tasks log in `<task>/.bytearmy/log.jsonl`. Each entry holds the current time in UTC (ISO 8601), its author (the name of the agent, or `user`) and the text. The user calls it from the command dialogue, an agent with `bytearmy log "<text>"`.]
- Show Log [web. Shows the tasks log as an HTML page, oldest entry first. bytearmy renders the page from `log.jsonl` and serves it under the same random key as the files of the HTML tool. When an entry is added the pane loads it again.]

What a program without a pane (an external tool, the VS Code server) prints goes to `<bytearmy_root>/.bytearmy/logs/<tool>.log`.


## Directory structure
Each task is assigned to a project and has its own working directory. 
`bytearmy_root` is `~/bytearmy` by default. It can be changed with the `--root` option or the `BYTEARMY_ROOT` environment variable.
If the root does not exist, bytearmy asks whether to create it and does not start without a yes, so a mistyped root does not start an empty bytearmy.
Only one bytearmy runs on a root at a time: a second one refuses to start and names the process that has the root. The claim on the root ends with that process, also when it crashes.

bytearmy_root
|- .tools [contains tools]
|- .claude [contains bytearmy.md, the explanation for claude about how bytearmy works and how claude can interact with it. It is passed to every Claude pane when it starts]
|- .bytearmy [contains global metadata, the logs of programs without a pane, the browser session of the web panes, the `bytearmy` command of the panes and the lock that keeps a second bytearmy off the root]
|-- project-1
    |- .bytearmy [contains bytearmy project level metadata, like name and Github URL]
    |- .claude [contains project specific claude configurations like CLAUDE.md etc. Claude loads this CLAUDE.md by itself in every task of the project]
    |- task-1
        |- .bytearmy [contains bytearmy metadata, like current state of the UI, and the instructions each agent of the task started with]
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
