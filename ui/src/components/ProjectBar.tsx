import {
  DndContext,
  type DragEndEvent,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
} from '@dnd-kit/core'
import {
  SortableContext,
  arrayMove,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { ChevronLeft, ChevronRight, Plus, Trash2 } from 'lucide-react'
import { useState } from 'react'
import type { Project, ProjectMeta } from '@/api/client'
import { BarHeading } from '@/components/BarHeading'
import { CreateProjectDialog } from '@/components/CreateProjectDialog'
import { DeleteProjectDialog } from '@/components/DeleteProjectDialog'
import { Button } from '@/components/ui/button'
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
  ContextMenuTrigger,
} from '@/components/ui/context-menu'
import { cn } from '@/lib/utils'

type Props = {
  open: boolean
  onToggle: () => void
  projects: Project[]
  focused: string | null
  onFocus: (slug: string) => void
  onCreate: (meta: ProjectMeta) => Promise<void>
  onDelete: (slug: string) => void
  onReorder: (order: string[]) => void
}

export function ProjectBar({ open, onToggle, projects, focused, onFocus, onCreate, onDelete, onReorder }: Props) {
  const [creating, setCreating] = useState(false)
  const [deleting, setDeleting] = useState<Project | null>(null)
  // A drag starts only after a few pixels of movement, so a plain click still focuses.
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  )

  if (!open) {
    return (
      <nav aria-label="Projects (collapsed)" className="flex w-12 shrink-0 flex-col items-center gap-3 border-r py-3">
        <Button variant="ghost" size="icon-sm" aria-label="Expand projects bar" onClick={onToggle}>
          <ChevronRight />
        </Button>
        <BarHeading vertical>Projects</BarHeading>
      </nav>
    )
  }

  const slugs = projects.map((project) => project.slug)

  function onDragEnd({ active, over }: DragEndEvent) {
    if (!over || active.id === over.id) return
    onReorder(arrayMove(slugs, slugs.indexOf(String(active.id)), slugs.indexOf(String(over.id))))
  }

  return (
    <nav aria-label="Projects" className="flex w-56 shrink-0 flex-col gap-2 border-r p-4">
      <div className="flex items-center gap-1">
        <div className="grow">
          <BarHeading>Projects</BarHeading>
        </div>
        <Button variant="ghost" size="icon-sm" aria-label="Add project" onClick={() => setCreating(true)}>
          <Plus />
        </Button>
        <Button variant="ghost" size="icon-sm" aria-label="Collapse projects bar" onClick={onToggle}>
          <ChevronLeft />
        </Button>
      </div>

      <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
        <SortableContext items={slugs} strategy={verticalListSortingStrategy}>
          <ul className="flex min-h-0 flex-col gap-1 overflow-y-auto">
            {projects.map((project) => (
              <ProjectItem
                key={project.slug}
                project={project}
                focused={project.slug === focused}
                onFocus={() => onFocus(project.slug)}
                onDelete={() => setDeleting(project)}
              />
            ))}
          </ul>
        </SortableContext>
      </DndContext>
      {projects.length === 0 && <p className="text-muted-foreground">No projects yet.</p>}

      <CreateProjectDialog open={creating} onOpenChange={setCreating} onCreate={onCreate} />
      <DeleteProjectDialog
        project={deleting}
        onCancel={() => setDeleting(null)}
        onConfirm={(project) => {
          setDeleting(null)
          onDelete(project.slug)
        }}
      />
    </nav>
  )
}

function ProjectItem({
  project,
  focused,
  onFocus,
  onDelete,
}: {
  project: Project
  focused: boolean
  onFocus: () => void
  onDelete: () => void
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: project.slug,
  })

  return (
    <li ref={setNodeRef} style={{ transform: CSS.Translate.toString(transform), transition }}>
      <ContextMenu>
        <ContextMenuTrigger asChild>
          <button
            type="button"
            aria-current={focused}
            className={cn(
              'w-full truncate rounded-md px-3 py-1.5 text-left outline-none focus-visible:ring-2',
              focused
                ? 'bg-white font-medium shadow-sm ring-1 ring-border'
                : 'text-muted-foreground hover:bg-zinc-100 hover:text-foreground',
              isDragging && 'relative z-10 cursor-grabbing shadow-md',
            )}
            onClick={onFocus}
            {...attributes}
            {...listeners}
          >
            {project.name}
          </button>
        </ContextMenuTrigger>
        <ContextMenuContent className="w-72">
          <ProjectData project={project} />
          <ContextMenuSeparator />
          <ContextMenuItem variant="destructive" onSelect={onDelete}>
            <Trash2 />
            Delete project…
          </ContextMenuItem>
        </ContextMenuContent>
      </ContextMenu>
    </li>
  )
}

function ProjectData({ project }: { project: Project }) {
  const rows = [
    ['Name', project.name],
    ['Description', project.description],
    ['GitHub URL', project.github_url],
    ['Directory', project.path],
  ].filter(([, value]) => value)

  return (
    <dl className="grid gap-2 px-2 py-1.5">
      {rows.map(([label, value]) => (
        <div key={label}>
          <dt className="text-xs text-muted-foreground">{label}</dt>
          <dd className="break-words whitespace-pre-wrap">{value}</dd>
        </div>
      ))}
    </dl>
  )
}
