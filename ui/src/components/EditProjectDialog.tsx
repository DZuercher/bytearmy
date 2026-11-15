import { type FormEvent, useState } from 'react'
import type { Project, ProjectEdit } from '@/api/client'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'

// Changes the name and description of a project. Shown while `project` is being edited.
export function EditProjectDialog({
  project,
  onClose,
  onSave,
}: {
  project: Project
  onClose: () => void
  onSave: (edit: ProjectEdit) => Promise<void>
}) {
  const [edit, setEdit] = useState<ProjectEdit>({ name: project.name, description: project.description })
  const [error, setError] = useState<string | null>(null)

  function submit(event: FormEvent) {
    event.preventDefault()
    onSave(edit).then(onClose, (reason: unknown) =>
      setError(reason instanceof Error ? reason.message : String(reason)),
    )
  }

  return (
    <Dialog open onOpenChange={(next) => !next && onClose()}>
      <DialogContent>
        <form onSubmit={submit} className="grid gap-4">
          <DialogHeader>
            <DialogTitle>Edit project</DialogTitle>
            <DialogDescription>The directory keeps its name. The GitHub URL cannot be changed.</DialogDescription>
          </DialogHeader>
          <div className="grid gap-2">
            <Label htmlFor="edit-project-name">Name</Label>
            <Input
              id="edit-project-name"
              autoFocus
              value={edit.name}
              onChange={(event) => setEdit({ ...edit, name: event.target.value })}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="edit-project-github-url">GitHub URL</Label>
            <Input id="edit-project-github-url" value={project.github_url} disabled />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="edit-project-description">Description</Label>
            <Textarea
              id="edit-project-description"
              value={edit.description}
              onChange={(event) => setEdit({ ...edit, description: event.target.value })}
            />
          </div>
          {error && <p className="text-destructive">{error}</p>}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={edit.name.trim() === ''}>
              Save
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
