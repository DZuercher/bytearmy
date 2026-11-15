import { ChevronDown, ChevronUp } from 'lucide-react'
import type { Project } from '@/api/client'
import { BarHeading } from '@/components/BarHeading'
import type { Doomed } from '@/components/DeleteDialog'
import { SortableItem, SortableList } from '@/components/Sortable'
import { TaskItem } from '@/components/TaskItem'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'

type Props = {
  open: boolean
  onToggle: () => void
  project: Project | undefined // the focused project
  onFocus: (slug: string) => void
  onCloneAgain: (slug: string) => void
  onDelete: (doomed: Doomed) => void
  onReorder: (order: string[]) => void
}

// Bottom bar: the started tasks of the focused project.
export function TaskBar({ open, onToggle, project, onFocus, onCloneAgain, onDelete, onReorder }: Props) {
  if (!open) {
    return (
      <footer className="flex h-9 shrink-0 items-center gap-4 border-t px-4">
        <BarHeading>Tasks</BarHeading>
        <div className="grow" />
        <Button variant="ghost" size="icon-sm" aria-label="Expand tasks bar" onClick={onToggle}>
          <ChevronUp />
        </Button>
      </footer>
    )
  }

  return (
    <footer aria-label="Tasks" className="flex h-20 shrink-0 items-center gap-4 border-t px-4">
      <BarHeading>Tasks</BarHeading>
      <div className="min-w-0 grow overflow-x-auto">
        {project && (
          <SortableList
            ids={project.tasks.map((task) => task.slug)}
            horizontal
            onReorder={onReorder}
            className="flex gap-1 py-1"
          >
            {project.tasks.map((task) => (
              <SortableItem key={task.slug} id={task.slug} className="w-44 shrink-0">
                {(handle, isDragging) => (
                  <TaskItem
                    task={task}
                    project={project}
                    highlighted={task.slug === project.focused_task}
                    handle={handle}
                    isDragging={isDragging}
                    onClick={() => onFocus(task.slug)}
                    onCloneAgain={() => onCloneAgain(task.slug)}
                    onDelete={onDelete}
                  >
                    <Badge variant="secondary">{task.status}</Badge>
                  </TaskItem>
                )}
              </SortableItem>
            ))}
          </SortableList>
        )}
      </div>
      <Button variant="ghost" size="icon-sm" aria-label="Collapse tasks bar" onClick={onToggle}>
        <ChevronDown />
      </Button>
    </footer>
  )
}
