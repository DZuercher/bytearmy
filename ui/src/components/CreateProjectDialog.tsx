import { type FormEvent, useState } from 'react'
import type { ProjectMeta } from '@/api/client'
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

const EMPTY: ProjectMeta = { name: '', github_url: '', description: '' }

export function CreateProjectDialog({
  open,
  onOpenChange,
  onCreate,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreate: (meta: ProjectMeta) => Promise<void>
}) {
  const [meta, setMeta] = useState(EMPTY)
  const [error, setError] = useState<string | null>(null)

  function close() {
    setMeta(EMPTY)
    setError(null)
    onOpenChange(false)
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    onCreate(meta).then(close, (reason: unknown) =>
      setError(reason instanceof Error ? reason.message : String(reason)),
    )
  }

  return (
    <Dialog open={open} onOpenChange={(next) => (next ? onOpenChange(true) : close())}>
      <DialogContent>
        <form onSubmit={submit} className="grid gap-4">
          <DialogHeader>
            <DialogTitle>New project</DialogTitle>
            <DialogDescription>Only the name is required.</DialogDescription>
          </DialogHeader>
          <div className="grid gap-2">
            <Label htmlFor="project-name">Name</Label>
            <Input
              id="project-name"
              autoFocus
              value={meta.name}
              onChange={(event) => setMeta({ ...meta, name: event.target.value })}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="project-github-url">GitHub URL</Label>
            <Input
              id="project-github-url"
              placeholder="git@github.com:owner/repo.git"
              value={meta.github_url}
              onChange={(event) => setMeta({ ...meta, github_url: event.target.value })}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="project-description">Description</Label>
            <Textarea
              id="project-description"
              value={meta.description}
              onChange={(event) => setMeta({ ...meta, description: event.target.value })}
            />
          </div>
          {error && <p className="text-destructive">{error}</p>}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={close}>
              Cancel
            </Button>
            <Button type="submit" disabled={meta.name.trim() === ''}>
              Create project
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
