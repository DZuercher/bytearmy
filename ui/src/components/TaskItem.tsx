import { Bell, LoaderCircle, RotateCw, Trash2, TriangleAlert } from 'lucide-react'
import type { HTMLAttributes, ReactNode } from 'react'
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
  onDelete,
  children,
}: {
  task: Task
  project: Project
  highlighted?: boolean
  handle: HTMLAttributes<HTMLElement>
  isDragging: boolean
  onClick: () => void
  onCloneAgain: () => void
  onDelete: (doomed: Doomed) => void
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
            highlighted
              ? 'bg-white font-medium shadow-sm ring-1 ring-border'
              : 'text-muted-foreground hover:bg-zinc-100 hover:text-foreground',
            isDragging && 'relative z-10 cursor-grabbing shadow-md',
          )}
          onClick={onClick}
          {...handle}
        >
          <span className="flex w-full items-center gap-1.5">
            <span className="truncate">{task.name}</span>
            <Markers task={task} />
          </span>
          {children}
        </button>
      </ContextMenuTrigger>
      <ContextMenuContent className="w-72">
        <DataList
          rows={[
            ['Name', task.name],
            ['Status', task.status],
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
        <ContextMenuItem variant="destructive" onSelect={() => onDelete({ kind: 'task', ...task })}>
          <Trash2 />
          Delete task…
        </ContextMenuItem>
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
