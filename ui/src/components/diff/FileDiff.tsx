import { ChevronDown, ChevronRight, MessageSquarePlus, Plus } from 'lucide-react'
import { type MouseEvent, type ReactNode, useMemo, useState } from 'react'
import type { DiffFile, DiffLine } from '@/api/client'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { type CommentActions, CommentThread } from './Comments'
import { StatusLetter } from './FileList'
import { highlightFile, type Token } from './highlight'
import {
  inRange,
  type LineRef,
  lineRef,
  type Numbered,
  pairLines,
  rangeLabel,
  refKey,
  refKeys,
  type Selection,
  sameRef,
  type View,
} from './model'

type Props = {
  file: DiffFile
  view: View
  comments: Numbered[] // the comments on this file
  selection: Selection | null // the selection, if it is in this file
  editing: number | null
  actions: CommentActions
  onSelect: (ref: LineRef, extend: boolean) => void
  onCommentFile: () => void
}

// The changes of one file: a header that stays on top while scrolling, then the hunks with
// the comments below the lines they are on.
export function FileDiff({ file, view, comments, selection, editing, actions, onSelect, onCommentFile }: Props) {
  const [collapsed, setCollapsed] = useState(false)
  const tokens = useMemo(() => highlightFile(file), [file])
  const keys = useMemo(() => refKeys(file), [file])
  const onFile = comments.filter(({ comment }) => comment.start === 0)
  // Comments on lines the diff no longer shows go to the top, so none gets lost.
  const lost = comments.filter(
    ({ comment }) => comment.start > 0 && !keys.has(refKey({ side: comment.side, line: comment.end })),
  )

  const lineState = (ref: LineRef) => ({
    selected: selection !== null && inRange(ref, selection),
    commented: comments.some(({ comment }) => inRange(ref, comment)),
  })

  const thread = (ref: LineRef) => {
    const here = comments.filter(
      ({ comment }) => comment.start > 0 && sameRef({ side: comment.side, line: comment.end }, ref),
    )
    const writing =
      selection !== null && selection.start > 0 && sameRef({ side: selection.side, line: selection.end }, ref)
    return (
      <CommentThread
        key={`thread-${refKey(ref)}`}
        comments={here}
        editing={editing}
        actions={actions}
        newLabel={writing ? rangeLabel(selection) : null}
      />
    )
  }

  const select = (line: DiffLine) => (event: MouseEvent) => onSelect(lineRef(line), event.shiftKey)

  return (
    <section className="border-b">
      <header className="sticky top-0 z-10 flex items-center gap-2 border-b bg-background px-2 py-1">
        <Button
          variant="ghost"
          size="icon-xs"
          aria-label={collapsed ? 'Expand file' : 'Collapse file'}
          onClick={() => setCollapsed(!collapsed)}
        >
          {collapsed ? <ChevronRight /> : <ChevronDown />}
        </Button>
        <StatusLetter status={file.status} />
        <span className="min-w-0 flex-1 truncate font-mono text-xs" title={file.path}>
          {file.old_path ? `${file.old_path} → ${file.path}` : file.path}
        </span>
        <span className="font-mono text-xs text-task-green-mark">+{file.additions}</span>
        <span className="font-mono text-xs text-task-red-mark">−{file.deletions}</span>
        <Button variant="ghost" size="xs" onClick={onCommentFile}>
          <MessageSquarePlus /> Comment on file
        </Button>
      </header>
      <CommentThread
        comments={[...onFile, ...lost]}
        editing={editing}
        actions={actions}
        newLabel={selection?.start === 0 ? 'file' : null}
      />
      {!collapsed && (
        <div className="font-mono text-xs leading-5">
          {file.binary ? (
            <Note>Binary file, not shown.</Note>
          ) : file.truncated ? (
            <Note>This file has too many changes to show.</Note>
          ) : (
            file.hunks.length === 0 && <Note>No changes in the content.</Note>
          )}
          {file.hunks.map((hunk, h) => (
            <div key={h}>
              <div className="bg-(--diff-hunk) px-2 py-0.5 text-muted-foreground">{hunk.header}</div>
              {view === 'unified'
                ? hunk.lines.flatMap((line, i) => {
                    const ref = lineRef(line)
                    return [
                      <UnifiedRow key={i} line={line} tokens={tokens[h][i]} {...lineState(ref)} onSelect={select(line)} />,
                      thread(ref),
                    ]
                  })
                : pairLines(hunk).flatMap((pair, p) => {
                    const left = pair.left === null ? null : hunk.lines[pair.left]
                    const right = pair.right === null ? null : hunk.lines[pair.right]
                    const refs = [left, right].filter((line) => line !== null).map(lineRef)
                    const distinct = refs.filter((ref, i) => i === 0 || !sameRef(ref, refs[0]))
                    return [
                      <div key={p} className="flex">
                        {[left, right].map((line, side) => {
                          const index = side === 0 ? pair.left : pair.right
                          return line === null || index === null ? (
                            <div key={side} className="w-1/2 bg-muted/40" />
                          ) : (
                            <HalfRow
                              key={side}
                              line={line}
                              number={side === 0 ? line.old : line.new}
                              tokens={tokens[h][index]}
                              {...lineState(lineRef(line))}
                              onSelect={select(line)}
                            />
                          )
                        })}
                      </div>,
                      ...distinct.map(thread),
                    ]
                  })}
            </div>
          ))}
        </div>
      )}
    </section>
  )
}

function Note({ children }: { children: ReactNode }) {
  return <p className="px-4 py-2 font-sans text-muted-foreground">{children}</p>
}

type RowProps = {
  line: DiffLine
  tokens: Token[]
  selected: boolean
  commented: boolean
  onSelect: (event: MouseEvent) => void
}

const SIGNS = { add: '+', delete: '−', context: ' ' } as const

function background(line: DiffLine, selected: boolean): string {
  if (selected) return 'bg-(--diff-selected)'
  return line.kind === 'add' ? 'bg-(--diff-add)' : line.kind === 'delete' ? 'bg-(--diff-delete)' : ''
}

function gutterBackground(line: DiffLine, selected: boolean, commented: boolean): string {
  if (selected) return 'bg-(--diff-selected-gutter)'
  if (commented) return 'bg-(--diff-commented-gutter)'
  return line.kind === 'add' ? 'bg-(--diff-add-gutter)' : line.kind === 'delete' ? 'bg-(--diff-delete-gutter)' : ''
}

function UnifiedRow({ line, tokens, selected, commented, onSelect }: RowProps) {
  const gutter = gutterBackground(line, selected, commented)
  return (
    <div className={cn('group flex', background(line, selected))}>
      <Gutter number={line.old} className={gutter} onSelect={onSelect} />
      <Gutter number={line.new} className={gutter} onSelect={onSelect} />
      <Code line={line} tokens={tokens} onSelect={onSelect} />
    </div>
  )
}

function HalfRow({ number, ...props }: RowProps & { number: number | null | undefined }) {
  const { line, selected, commented, onSelect } = props
  return (
    <div className={cn('group flex w-1/2 min-w-0', background(line, selected))}>
      <Gutter number={number} className={gutterBackground(line, selected, commented)} onSelect={onSelect} />
      <Code {...props} />
    </div>
  )
}

type GutterProps = { number: number | null | undefined; className: string; onSelect: (event: MouseEvent) => void }

// The line number; clicking it selects the line, shift-clicking extends the selection.
function Gutter({ number, className, onSelect }: GutterProps) {
  return (
    <button
      type="button"
      tabIndex={-1}
      className={cn(
        'w-12 shrink-0 cursor-pointer px-2 text-right text-muted-foreground select-none hover:text-foreground',
        className,
      )}
      // A shift-click would select text otherwise.
      onMouseDown={(event) => event.shiftKey && event.preventDefault()}
      onClick={onSelect}
    >
      {number ?? ''}
    </button>
  )
}

function Code({ line, tokens, onSelect }: Pick<RowProps, 'line' | 'tokens' | 'onSelect'>) {
  return (
    <div className="relative flex min-w-0 flex-1">
      <button
        type="button"
        aria-label="Comment on this line"
        className="absolute top-0.5 -left-2 hidden size-4 cursor-pointer items-center justify-center rounded bg-task-blue-mark text-white group-hover:flex"
        onMouseDown={(event) => event.shiftKey && event.preventDefault()}
        onClick={onSelect}
      >
        <Plus className="size-3" />
      </button>
      <span className="w-4 shrink-0 text-center text-muted-foreground select-none">{SIGNS[line.kind]}</span>
      <code className="min-w-0 flex-1 pr-2 break-all whitespace-pre-wrap">
        {tokens.map((token, i) =>
          token.className ? (
            <span key={i} className={token.className}>
              {token.text}
            </span>
          ) : (
            token.text
          ),
        )}
      </code>
    </div>
  )
}
