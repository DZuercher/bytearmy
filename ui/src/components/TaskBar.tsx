import { ChevronDown, ChevronLeft, ChevronRight } from 'lucide-react'
import type { Project, Task } from '@/api/client'
import { BarHeading } from '@/components/BarHeading'
import type { Doomed } from '@/components/DeleteDialog'
import { SortableItem, SortableList } from '@/components/Sortable'
import { TaskItem } from '@/components/TaskItem'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

type Props = {
  open: boolean
  onToggle: () => void
  project: Project | undefined // the focused project
  onFocus: (slug: string) => void
  onCloneAgain: (slug: string) => void
  onEdit: (slug: string) => void
  onDelete: (doomed: Doomed) => void
  onReorder: (order: string[]) => void
  onListHidden: (slug: string, open: boolean) => void // unfold or fold a task's hidden panes
  onShowPane: (slug: string, pane: string) => void // put a hidden pane back on the workspace
}

// Left-hand bar, right of the projects: the started tasks of the focused project.
export function TaskBar({
  open,
  onToggle,
  project,
  onFocus,
  onCloneAgain,
  onEdit,
  onDelete,
  onReorder,
  onListHidden,
  onShowPane,
}: Props) {
  if (!open) {
    return (
      <nav aria-label="Tasks (collapsed)" className="flex w-12 shrink-0 flex-col items-center gap-3 border-r py-3">
        <Button variant="ghost" size="icon-sm" aria-label="Expand tasks bar" onClick={onToggle}>
          <ChevronRight />
        </Button>
        <BarHeading vertical>Tasks</BarHeading>
      </nav>
    )
  }

  return (
    <nav aria-label="Tasks" className="flex w-56 shrink-0 flex-col gap-2 border-r p-4">
      <div className="flex items-center gap-1">
        <div className="grow">
          <BarHeading>Tasks</BarHeading>
        </div>
        <Button variant="ghost" size="icon-sm" aria-label="Collapse tasks bar" onClick={onToggle}>
          <ChevronLeft />
        </Button>
      </div>

      {project && (
        <SortableList
          ids={project.tasks.map((task) => task.slug)}
          onReorder={onReorder}
          className="flex min-h-0 flex-col gap-1 overflow-x-hidden overflow-y-auto"
        >
          {project.tasks.map((task) => {
            const hidden = hiddenPanes(task)
            const focused = task.slug === project.focused_task
            const unfolded = hidden.length > 0 && task.workspace.hidden_open
            return (
              <SortableItem key={task.slug} id={task.slug}>
                {(handle, isDragging) => (
                  <>
                    <div className="relative">
                      <TaskItem
                        task={task}
                        project={project}
                        highlighted={focused}
                        handle={handle}
                        isDragging={isDragging}
                        onClick={() => onFocus(task.slug)}
                        onCloneAgain={() => onCloneAgain(task.slug)}
                        onEdit={() => onEdit(task.slug)}
                        onDelete={onDelete}
                        className={cn(hidden.length > 0 && 'pr-8')}
                      >
                        <Badge variant="secondary">{task.status}</Badge>
                      </TaskItem>
                      {hidden.length > 0 && (
                        <Button
                          variant="ghost"
                          size="icon-xs"
                          aria-label={unfolded ? 'Fold hidden panes' : 'Unfold hidden panes'}
                          aria-expanded={unfolded}
                          className={cn(
                            'absolute top-1.5 right-1',
                            focused && 'text-primary-foreground hover:bg-primary-hover hover:text-primary-foreground',
                          )}
                          onClick={() => onListHidden(task.slug, !unfolded)}
                        >
                          {unfolded ? <ChevronDown /> : <ChevronRight />}
                        </Button>
                      )}
                    </div>
                    {unfolded && (
                      <ul aria-label={`Hidden panes of ${task.name}`} className="mt-0.5 mb-1 ml-4 flex flex-col">
                        {hidden.map((pane) => (
                          <li key={pane.id}>
                            <button
                              type="button"
                              className="w-full truncate rounded-md px-2 py-0.5 text-left text-sm text-muted-foreground hover:bg-hover hover:text-foreground"
                              onClick={() => onShowPane(task.slug, pane.id)}
                            >
                              {pane.title}
                            </button>
                          </li>
                        ))}
                      </ul>
                    )}
                  </>
                )}
              </SortableItem>
            )
          })}
        </SortableList>
      )}
    </nav>
  )
}

// The panes of a task that have no place on its workspace, in the order they were opened.
function hiddenPanes(task: Task) {
  return Object.values(task.workspace.panes ?? {}).filter((pane) => pane.hidden)
}
