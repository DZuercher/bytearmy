import { ChevronLeft, ChevronRight, Pencil, Plus, Trash2 } from 'lucide-react'
import { useState } from 'react'
import type { Project, ProjectEdit } from '@/api/client'
import { BarHeading } from '@/components/BarHeading'
import { DataList } from '@/components/DataList'
import type { Doomed } from '@/components/DeleteDialog'
import { EditProjectDialog } from '@/components/EditProjectDialog'
import { SortableItem, SortableList } from '@/components/Sortable'
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
  onAdd: () => void // opens the dialog that creates a project
  onEdit: (slug: string, edit: ProjectEdit) => Promise<void>
  onDelete: (doomed: Doomed) => void
  onReorder: (order: string[]) => void
}

export function ProjectBar({ open, onToggle, projects, focused, onFocus, onAdd, onEdit, onDelete, onReorder }: Props) {
  const [editing, setEditing] = useState<string | null>(null) // the slug of the project being edited
  const edited = projects.find((project) => project.slug === editing)

  if (!open) {
    return (
      <nav aria-label="Projects (collapsed)" className="flex min-h-0 w-12 flex-1 flex-col items-center gap-3 border-r py-3">
        <Button variant="ghost" size="icon-sm" aria-label="Expand projects bar" onClick={onToggle}>
          <ChevronRight />
        </Button>
        <BarHeading vertical>Projects</BarHeading>
      </nav>
    )
  }

  return (
    <nav aria-label="Projects" className="flex min-h-0 w-56 flex-1 flex-col gap-2 border-r p-4">
      <div className="flex items-center gap-1">
        <div className="grow">
          <BarHeading>Projects</BarHeading>
        </div>
        <Button variant="ghost" size="icon-sm" aria-label="Add project" onClick={onAdd}>
          <Plus />
        </Button>
        <Button variant="ghost" size="icon-sm" aria-label="Collapse projects bar" onClick={onToggle}>
          <ChevronLeft />
        </Button>
      </div>

      <SortableList
        ids={projects.map((project) => project.slug)}
        onReorder={onReorder}
        className="flex min-h-0 flex-col gap-1 overflow-x-hidden overflow-y-auto"
      >
        {projects.map((project) => (
          <SortableItem key={project.slug} id={project.slug}>
            {(handle, isDragging) => (
              <ContextMenu>
                <ContextMenuTrigger asChild>
                  <button
                    type="button"
                    aria-current={project.slug === focused}
                    className={cn(
                      'w-full truncate rounded-md px-3 py-1.5 text-left outline-none focus-visible:ring-2',
                      project.slug === focused
                        ? 'bg-primary font-medium text-primary-foreground shadow-sm'
                        : 'text-muted-foreground hover:bg-hover hover:text-foreground',
                      isDragging && 'relative z-10 cursor-grabbing shadow-md',
                    )}
                    onClick={() => onFocus(project.slug)}
                    {...handle}
                  >
                    {project.name}
                  </button>
                </ContextMenuTrigger>
                <ContextMenuContent className="w-72">
                  <DataList
                    rows={[
                      ['Name', project.name],
                      ['Description', project.description],
                      ['GitHub URL', project.github_url],
                      ['Directory', project.path],
                    ]}
                  />
                  <ContextMenuSeparator />
                  <ContextMenuItem onSelect={() => setEditing(project.slug)}>
                    <Pencil />
                    Edit project…
                  </ContextMenuItem>
                  <ContextMenuItem
                    variant="destructive"
                    onSelect={() => onDelete({ kind: 'project', ...project })}
                  >
                    <Trash2 />
                    Delete project…
                  </ContextMenuItem>
                </ContextMenuContent>
              </ContextMenu>
            )}
          </SortableItem>
        ))}
      </SortableList>
      {projects.length === 0 && <p className="text-muted-foreground">No projects yet.</p>}

      {edited && (
        <EditProjectDialog
          key={edited.slug}
          project={edited}
          onClose={() => setEditing(null)}
          onSave={(edit) => onEdit(edited.slug, edit)}
        />
      )}
    </nav>
  )
}
