import { PanelLeftClose, PanelLeftOpen, RefreshCw } from 'lucide-react'
import { type ReactNode, useEffect, useMemo, useRef, useState } from 'react'
import {
  type AppState,
  api,
  type Diff,
  type DiffFile,
  type DiffMode,
  type Pane,
  paneDiff,
  paneDiffFile,
  paneReview,
  type Review,
  type ReviewComment,
  saveReview,
} from '@/api/client'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import type { CommentActions } from './Comments'
import { FileDiff } from './FileDiff'
import { FileList } from './FileList'
import { type LineRef, type Numbered, quote, type Selection, type View } from './model'
import { ReviewDialog } from './ReviewDialog'

const SAVE_DELAY_MS = 500 // the draft is saved this long after the last change

type Props = {
  place: { project: string; task: string; pane: string }
  pane: Pane
  run: (call: Promise<AppState>) => void
}

// Shows the changes of the task's clone. The user comments on lines, ranges and files and
// finishes the review; the core sends the comments to the agent that asked for it.
export function DiffPane({ place, pane, run }: Props) {
  const { project, task, pane: id } = place
  const [review, setReview] = useState<Review | null>(null) // null until the draft is loaded
  const [diff, setDiff] = useState<{ diff?: Diff; error?: string; loading: boolean }>({ loading: true })
  const [reload, setReload] = useState(0)
  const [view, setView] = useState<View>('unified')
  const [sidebar, setSidebar] = useState(true)
  const [selection, setSelection] = useState<Selection | null>(null)
  const [editing, setEditing] = useState<number | null>(null)
  const [finishing, setFinishing] = useState(false)
  // The files the user expanded to all their lines, by path.
  const [full, setFull] = useState<Record<string, FullFile>>({})
  const files = useRef<Record<string, HTMLDivElement | null>>({})
  const saveTimer = useRef<ReturnType<typeof setTimeout>>(undefined)
  const mode = review?.mode

  // The draft decides the mode once it has something in it; the comments are on its lines.
  useEffect(() => {
    let current = true
    const initial: DiffMode = pane.values.mode === 'uncommitted' ? 'uncommitted' : 'branch'
    const fresh: Review = { mode: initial, comments: [], summary: '' }
    paneReview({ project, task, pane: id }).then(
      (draft) => current && setReview(draft.comments.length || draft.summary ? draft : fresh),
      () => current && setReview(fresh),
    )
    return () => {
      current = false
    }
    // Only the first mode counts; the user switches it here afterwards.
  }, [project, task, id])

  useEffect(() => {
    if (!mode) return
    let current = true
    setDiff((old) => ({ ...old, loading: true }))
    setFull({})
    paneDiff({ project, task, pane: id }, mode).then(
      (answer) => current && setDiff({ diff: answer, loading: false }),
      (reason: unknown) =>
        current && setDiff({ error: reason instanceof Error ? reason.message : String(reason), loading: false }),
    )
    return () => {
      current = false
    }
  }, [project, task, id, mode, reload])

  // Every change to the draft is saved after a short pause, so it survives a reload.
  const loaded = useRef(false)
  useEffect(() => {
    if (!review) return
    if (!loaded.current) {
      loaded.current = true // the draft just came from the core
      return
    }
    clearTimeout(saveTimer.current)
    saveTimer.current = setTimeout(() => saveReview({ project, task, pane: id }, review).catch(() => {}), SAVE_DELAY_MS)
  }, [review, project, task, id])
  useEffect(() => () => clearTimeout(saveTimer.current), [])

  const byFile = useMemo(() => {
    const groups: Record<string, Numbered[]> = {}
    review?.comments.forEach((comment, index) => (groups[comment.path] ??= []).push({ comment, index }))
    return groups
  }, [review])
  const counts = useMemo(
    () => Object.fromEntries(Object.entries(byFile).map(([path, list]) => [path, list.length])),
    [byFile],
  )

  if (!review) return <Message>Loading…</Message>

  const change = (next: Partial<Review>) => setReview((old) => old && { ...old, ...next })
  const comments = review.comments

  const actions: CommentActions = {
    save: (body) => {
      const file = shownFiles.find((candidate) => candidate.path === selection?.path)
      if (!selection || !file) return
      const { path, side, start, end } = selection
      const comment: ReviewComment = { path, side, start, end, quote: quote(file, selection), body }
      change({ comments: [...comments, comment] })
      setSelection(null)
    },
    cancel: () => setSelection(null),
    edit: (index) => {
      setEditing(index)
      if (index !== null) setSelection(null)
    },
    update: (index, body) => {
      change({ comments: comments.map((comment, i) => (i === index ? { ...comment, body } : comment)) })
      setEditing(null)
    },
    remove: (index) => {
      change({ comments: comments.filter((_, i) => i !== index) })
      setEditing(null)
    },
  }

  function select(path: string, ref: LineRef, extend: boolean) {
    setEditing(null)
    if (extend && selection && selection.path === path && selection.side === ref.side && selection.start > 0) {
      const { anchor } = selection
      setSelection({ ...selection, start: Math.min(anchor, ref.line), end: Math.max(anchor, ref.line) })
    } else {
      setSelection({ path, side: ref.side, start: ref.line, end: ref.line, anchor: ref.line })
    }
  }

  function expand(file: DiffFile) {
    if (full[file.path]) {
      setFull(({ [file.path]: _, ...rest }) => rest)
      return
    }
    setFull((old) => ({ ...old, [file.path]: { loading: true } }))
    const answer = (entry: FullFile) =>
      // Only if the user did not collapse it again, nor the diff was loaded anew meanwhile.
      setFull((old) => (old[file.path]?.loading ? { ...old, [file.path]: entry } : old))
    paneDiffFile({ project, task, pane: id }, review!.mode, file).then(
      (found) => answer({ file: found, loading: false }),
      (reason: unknown) =>
        answer({ loading: false, error: reason instanceof Error ? reason.message : String(reason) }),
    )
  }

  function submit() {
    clearTimeout(saveTimer.current)
    const call = api.submitReview(project, task, id, review!)
    run(call)
    return call
  }

  const shown = diff.diff
  // The files as they are shown: those the user expanded with all their lines.
  const shownFiles = (shown?.files ?? []).map((file) => full[file.path]?.file ?? file)
  return (
    <div className="flex size-full flex-col bg-background">
      <div className="flex shrink-0 flex-wrap items-center gap-2 border-b px-2 py-1">
        <Button
          variant="ghost"
          size="icon-xs"
          aria-label={sidebar ? 'Hide file list' : 'Show file list'}
          onClick={() => setSidebar(!sidebar)}
        >
          {sidebar ? <PanelLeftClose /> : <PanelLeftOpen />}
        </Button>
        <Toggle
          options={[
            { value: 'branch', label: `Since ${shown?.base || 'base'}` },
            { value: 'uncommitted', label: 'Uncommitted' },
          ]}
          value={review.mode}
          onChange={(next) => {
            setSelection(null)
            change({ mode: next })
          }}
        />
        <Toggle
          options={[
            { value: 'unified', label: 'Unified' },
            { value: 'split', label: 'Split' },
          ]}
          value={view}
          onChange={setView}
        />
        <Button variant="ghost" size="icon-xs" aria-label="Refresh" onClick={() => setReload(reload + 1)}>
          <RefreshCw className={cn(diff.loading && 'animate-spin')} />
        </Button>
        <span className="flex-1" />
        <span className="text-xs text-muted-foreground">
          {comments.length} {comments.length === 1 ? 'comment' : 'comments'}
        </span>
        <Button size="sm" onClick={() => setFinishing(true)}>
          Finish review
        </Button>
      </div>
      {pane.values.note && (
        <div className="shrink-0 border-b bg-task-blue px-3 py-2 text-sm whitespace-pre-wrap">
          {pane.values.note}
        </div>
      )}
      {diff.error ? (
        <Message>{diff.error}</Message>
      ) : !shown ? (
        <Message>Loading…</Message>
      ) : shown.files.length === 0 ? (
        <Message>No changes.</Message>
      ) : (
        <div className="flex min-h-0 flex-1">
          {sidebar && (
            <nav className="w-60 shrink-0 overflow-y-auto border-r">
              <FileList
                files={shown.files}
                counts={counts}
                onPick={(path) => files.current[path]?.scrollIntoView({ block: 'start' })}
              />
            </nav>
          )}
          <div className="flex min-w-0 flex-1 flex-col gap-4 overflow-y-auto p-3">
            {shownFiles.map((file) => (
              <div key={file.path} ref={(element) => void (files.current[file.path] = element)}>
                <FileDiff
                  file={file}
                  full={full[file.path] ?? null}
                  onExpand={() => expand(file)}
                  view={view}
                  comments={byFile[file.path] ?? []}
                  selection={selection?.path === file.path ? selection : null}
                  editing={editing}
                  actions={actions}
                  onSelect={(ref, extend) => select(file.path, ref, extend)}
                  onCommentFile={() => {
                    setEditing(null)
                    setSelection({ path: file.path, side: 'new', start: 0, end: 0, anchor: 0 })
                  }}
                />
              </div>
            ))}
          </div>
        </div>
      )}
      {finishing && (
        <ReviewDialog
          comments={comments}
          summary={review.summary}
          recipient={pane.values.requester || 'the master agent'}
          onSummary={(summary) => change({ summary })}
          onClose={() => setFinishing(false)}
          onSubmit={submit}
        />
      )}
    </div>
  )
}

// A file the user expanded: loading, loaded with all its lines, or failed.
export type FullFile = { file?: DiffFile; loading: boolean; error?: string }

function Message({ children }: { children: ReactNode }) {
  return <p className="p-4 text-sm whitespace-pre-wrap text-muted-foreground">{children}</p>
}

type ToggleProps<Value extends string> = {
  options: { value: Value; label: string }[]
  value: Value
  onChange: (value: Value) => void
}

// Buttons of which one is pressed.
function Toggle<Value extends string>({ options, value, onChange }: ToggleProps<Value>) {
  return (
    <div className="flex rounded-lg border p-0.5">
      {options.map((option) => (
        <Button
          key={option.value}
          variant={option.value === value ? 'secondary' : 'ghost'}
          size="xs"
          aria-pressed={option.value === value}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </Button>
      ))}
    </div>
  )
}
