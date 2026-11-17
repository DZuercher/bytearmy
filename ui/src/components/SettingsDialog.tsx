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

// Edits the files of the root that hold the settings: config.toml and the instructions of
// the orchestrator, the masters and the other agents. Each file is saved on its own;
// a config.toml that would not hold is refused with the reason. "Reset to default" puts
// what bytearmy comes with into the editor, to be saved like any edit. Closing with unsaved
// edits asks first.
export function SettingsDialog({
  onClose,
  onSave,
}: {
  onClose: () => void
  onSave: (file: string, text: string) => Promise<void>
}) {
  const [files, setFiles] = useState<SettingsFile[] | null>(null)
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [shown, setShown] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [leaving, setLeaving] = useState(false) // asking whether to drop unsaved edits

  useEffect(() => {
    settings().then(
      (loaded) => {
        setFiles(loaded)
        setShown(loaded[0]?.id ?? null)
      },
      (reason: unknown) => setError(reason instanceof Error ? reason.message : String(reason)),
    )
  }, [])

  const file = files?.find((candidate) => candidate.id === shown)
  const text = file ? (drafts[file.id] ?? file.text) : ''
  const changed = (candidate: SettingsFile) =>
    drafts[candidate.id] !== undefined && drafts[candidate.id] !== candidate.text
  const unsaved = files?.filter(changed) ?? []
  const close = () => (unsaved.length > 0 ? setLeaving(true) : onClose())

  function save() {
    if (!file) return
    setSaving(true)
    setError(null)
    onSave(file.id, text).then(
      () => {
        setFiles((current) => current?.map((other) => (other.id === file.id ? { ...other, text } : other)) ?? null)
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
          <div role="tablist" className="flex gap-1 border-b">
            {files?.map((candidate) => (
              <button
                key={candidate.id}
                type="button"
                role="tab"
                aria-selected={candidate.id === shown}
                className={cn(
                  '-mb-px border-b-2 px-3 py-1.5 outline-none focus-visible:ring-2',
                  candidate.id === shown
                    ? 'border-primary font-medium text-foreground'
                    : 'border-transparent text-muted-foreground hover:text-foreground',
                )}
                onClick={() => {
                  setShown(candidate.id)
                  setError(null)
                }}
              >
                {candidate.label}
                {changed(candidate) && ' •'}
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
                onChange={(event) => setDrafts({ ...drafts, [file.id]: event.target.value })}
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
              onClick={() => file && setDrafts({ ...drafts, [file.id]: file.default })}
            >
              Reset to default
            </Button>
            <Button type="button" variant="outline" onClick={close}>
              Close
            </Button>
            <Button type="button" disabled={!file || !changed(file) || saving} onClick={save}>
              Save {file?.label}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <AlertDialog open={leaving} onOpenChange={(open) => !open && setLeaving(false)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Discard unsaved changes?</AlertDialogTitle>
            <AlertDialogDescription>
              Not saved: {unsaved.map((candidate) => candidate.label).join(', ')}.
            </AlertDialogDescription>
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
