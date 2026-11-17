import { useEffect, useState } from 'react'
import { type SettingsFile, settings } from '@/api/client'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'

// Edits the files that hold the settings: those of the root (config.toml and the
// instructions of the orchestrator, the masters and the other agents) or, picked at the
// top, those of a project (its config.toml, which overrides the root's for its tasks, and
// what it appends to the instructions of its masters, other agents and scratchpad agents).
// Each file is saved on its own; a config.toml that would not hold is refused with the
// reason. "Reset to default" puts what bytearmy comes with into the editor, to be saved like
// any edit. Edits survive switching between root and projects; closing with unsaved edits
// asks first.
export function SettingsDialog({
  projects,
  onClose,
  onSave,
}: {
  projects: { slug: string; name: string }[]
  onClose: () => void
  // scope: "" for the root, else the slug of a project
  onSave: (scope: string, file: string, text: string) => Promise<void>
}) {
  const [scope, setScope] = useState('')
  const [loaded, setLoaded] = useState<Record<string, SettingsFile[]>>({}) // by scope
  const [drafts, setDrafts] = useState<Record<string, string>>({}) // by `scope/file`
  const [shown, setShown] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [leaving, setLeaving] = useState(false) // asking whether to drop unsaved edits

  useEffect(() => {
    if (loaded[scope]) return
    settings(scope || undefined).then(
      (files) => setLoaded((current) => ({ ...current, [scope]: files })),
      (reason: unknown) => setError(reason instanceof Error ? reason.message : String(reason)),
    )
  }, [scope, loaded])

  const files = loaded[scope] ?? null
  const file = files?.find((candidate) => candidate.id === shown) ?? files?.[0]
  const key = (where: string, candidate: SettingsFile) => `${where}/${candidate.id}`
  const text = file ? (drafts[key(scope, file)] ?? file.text) : ''
  const changed = (where: string, candidate: SettingsFile) =>
    drafts[key(where, candidate)] !== undefined && drafts[key(where, candidate)] !== candidate.text
  const nameOf = (where: string) => projects.find((project) => project.slug === where)?.name ?? where
  const unsaved = Object.entries(loaded).flatMap(([where, candidates]) =>
    candidates
      .filter((candidate) => changed(where, candidate))
      .map((candidate) => (where ? `${candidate.label} of ${nameOf(where)}` : candidate.label)),
  )
  const close = () => (unsaved.length > 0 ? setLeaving(true) : onClose())

  function save() {
    if (!file) return
    const where = scope
    setSaving(true)
    setError(null)
    onSave(where, file.id, text).then(
      () => {
        setLoaded((current) => ({
          ...current,
          [where]: current[where].map((other) => (other.id === file.id ? { ...other, text } : other)),
        }))
        setSaving(false)
      },
      (reason: unknown) => {
        setError(reason instanceof Error ? reason.message : String(reason))
        setSaving(false)
      },
    )
  }

  return (
    <>
      <Dialog open onOpenChange={(next) => !next && close()}>
        <DialogContent className="flex h-[85vh] flex-col sm:max-w-4xl">
          <DialogHeader>
            <DialogTitle>Settings</DialogTitle>
            <DialogDescription>
              Changes count from the moment they are saved; agents that run already keep how they were started.
            </DialogDescription>
          </DialogHeader>
          <select
            aria-label="Settings of"
            className="h-8 w-full rounded-lg border border-input bg-transparent px-2.5 py-1 md:text-sm"
            value={scope}
            onChange={(event) => {
              setScope(event.target.value)
              setError(null)
            }}
          >
            <option value="">Global: all projects</option>
            {projects.map((project) => (
              <option key={project.slug} value={project.slug}>
                Project: {project.name}
              </option>
            ))}
          </select>
          {scope && (
            <p className="text-xs text-muted-foreground">
              The project's config overrides the global one for its tasks, except color_scheme, [orchestrator] and
              [inbox], which count only globally. Its prompts are appended to the global ones.
            </p>
          )}
          <div role="tablist" className="flex gap-1 border-b">
            {files?.map((candidate) => (
              <button
                key={candidate.id}
                type="button"
                role="tab"
                aria-selected={candidate.id === file?.id}
                className={cn(
                  '-mb-px border-b-2 px-3 py-1.5 outline-none focus-visible:ring-2',
                  candidate.id === file?.id
                    ? 'border-primary font-medium text-foreground'
                    : 'border-transparent text-muted-foreground hover:text-foreground',
                )}
                onClick={() => {
                  setShown(candidate.id)
                  setError(null)
                }}
              >
                {candidate.label}
                {changed(scope, candidate) && ' •'}
              </button>
            ))}
          </div>
          {file && (
            <>
              <p className="truncate font-mono text-xs text-muted-foreground" title={file.path}>
                {file.path}
              </p>
              <Textarea
                aria-label={file.label}
                spellCheck={false}
                className="min-h-0 flex-1 resize-none font-mono text-xs [field-sizing:fixed] md:text-xs"
                value={text}
                onChange={(event) => setDrafts({ ...drafts, [key(scope, file)]: event.target.value })}
              />
            </>
          )}
          {error && <p className="text-destructive">{error}</p>}
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              className="sm:mr-auto"
              disabled={!file || text === file.default}
              onClick={() => file && setDrafts({ ...drafts, [key(scope, file)]: file.default })}
            >
              Reset to default
            </Button>
            <Button type="button" variant="outline" onClick={close}>
              Close
            </Button>
            <Button type="button" disabled={!file || !changed(scope, file) || saving} onClick={save}>
              Save {file?.label}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <AlertDialog open={leaving} onOpenChange={(open) => !open && setLeaving(false)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Discard unsaved changes?</AlertDialogTitle>
            <AlertDialogDescription>Not saved: {unsaved.join(', ')}.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep editing</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={onClose}>
              Discard
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
