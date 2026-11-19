import { Bell, LoaderCircle, Pencil, RotateCw, Trash2, TriangleAlert } from 'lucide-react'
import type { HTMLAttributes, ReactNode, SyntheticEvent } from 'react'
import type { Project, Task } from '@/api/client'
import { DataList } from '@/components/DataList'
import type { Doomed } from '@/components/DeleteDialog'
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
  ContextMenuTrigger,
} from '@/components/ui/context-menu'
import { STATES } from '@/lib/states'
import { cn } from '@/lib/utils'

// A task in the backlog or in the task bar: click acts, right-click shows its data.
// `children` is what the bar adds below the name: the blocked note or the status tag.
export function TaskItem({
  task,
  project,
  highlighted = false,
  handle,
  isDragging,
  onClick,
  onCloneAgain,
  onSync,
  onEdit,
  onDelete,
  className,
  icon,
  children,
}: {
  task: Task
  project: Project
  highlighted?: boolean
  handle: HTMLAttributes<HTMLElement>
  isDragging: boolean
  onClick: () => void
  onCloneAgain: () => void
  onSync?: () => void // a click on the git status: the master merges in origin's commits
  onEdit: () => void
  onDelete: (doomed: Doomed) => void
  className?: string
  icon?: ReactNode // shown before the name
  children?: ReactNode
}) {
  return (
    <ContextMenu>
      <ContextMenuTrigger asChild>
        <button
          type="button"
          aria-current={highlighted}
          className={cn(
            'flex w-full flex-col items-start gap-1 rounded-md px-3 py-1.5 text-left outline-none focus-visible:ring-2',
            !highlighted && 'text-muted-foreground hover:bg-hover hover:text-foreground',
            STATES[task.state].color && cn('border-l-4 text-foreground', STATES[task.state].color),
            // After the color, so that a focused task stays dark; it keeps its colored left border.
            highlighted && 'bg-primary font-medium text-primary-foreground shadow-sm',
            isDragging && 'relative z-10 cursor-grabbing shadow-md',
            className,
          )}
          onClick={onClick}
          {...handle}
        >
          <span className="flex w-full items-center gap-1.5">
            {icon}
            <span className="truncate">{task.name}</span>
            <Markers task={task} />
          </span>
          <GitLine task={task} onSync={onSync} />
          {children}
        </button>
      </ContextMenuTrigger>
      <ContextMenuContent className="w-72">
        <DataList
          rows={[
            ['Name', task.name],
            ['Status', `${task.status} (${STATES[task.state].label})`],
            ['Description', task.description],
            ['Branch', task.branch],
            ['Reminder', task.reminder && `${task.reminder}${task.reminder_due ? ' (due)' : ''}`],
            ['Depends on', dependencyNames(task, project).join(', ')],
            ['Directory', task.path],
            ['Repository', repository(task)],
          ]}
        />
        <ContextMenuSeparator />
        {task.clone === 'failed' && (
          <ContextMenuItem onSelect={onCloneAgain}>
            <RotateCw />
            Clone again
          </ContextMenuItem>
        )}
        <ContextMenuItem onSelect={onEdit}>
          <Pencil />
          Edit task…
        </ContextMenuItem>
        {!task.scratchpad && (
          <ContextMenuItem variant="destructive" onSelect={() => onDelete({ kind: 'task', ...task })}>
            <Trash2 />
            Delete task…
          </ContextMenuItem>
        )}
      </ContextMenuContent>
    </ContextMenu>
  )
}

function Markers({ task }: { task: Task }) {
  return (
    <>
      {task.reminder_due && <Bell aria-label="Reminder is due" className="size-3.5 shrink-0 text-amber-600" />}
      {task.clone === 'cloning' && (
        <LoaderCircle aria-label="Cloning" className="size-3.5 shrink-0 animate-spin" />
      )}
      {task.clone === 'failed' && (
        <TriangleAlert aria-label="Clone failed" className="size-3.5 shrink-0 text-destructive" />
      )}
    </>
  )
}

// Where the task's clone stands against origin; nothing while it is clean and in step.
// A click has the master stash, merge in origin's commits and restore the stash.
function GitLine({ task, onSync }: { task: Task; onSync?: () => void }) {
  const git = task.git
  if (!git || (!git.dirty && !git.ahead && !git.behind)) return null
  // Inside the task's button, so not a button itself; the click must not focus the task.
  const sync = (event: SyntheticEvent) => {
    event.stopPropagation()
    onSync?.()
  }
  return (
    <span
      role="button"
      tabIndex={0}
      title="Have the master merge in the remote's commits"
      className="flex items-center gap-2 rounded-sm text-xs tabular-nums hover:underline"
      onClick={sync}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          sync(event)
        }
      }}
    >
      {git.dirty && <span title="Uncommitted changes">●</span>}
      {git.ahead > 0 && <span title={`${git.ahead} commits not on the remote`}>↑{git.ahead}</span>}
      {git.behind > 0 && <span title={`${git.behind} commits on the remote, not local`}>↓{git.behind}</span>}
    </span>
  )
}

function dependencyNames(task: Task, project: Project): string[] {
  const names = new Map([...project.backlog, ...project.tasks].map((other) => [other.slug, other.name]))
  return (task.dependencies ?? []).map((slug) => names.get(slug) ?? slug)
}

function repository(task: Task): string {
  if (task.clone === 'cloning') return `Cloning ${task.repo}…`
  if (task.clone === 'failed') return `Clone failed: ${task.clone_error}`
  if (task.clone === 'ready') return `${task.path}/${task.repo}`
  return ''
}
