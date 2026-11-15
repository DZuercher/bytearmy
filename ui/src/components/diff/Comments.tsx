import { Pencil, Trash2 } from 'lucide-react'
import { useState } from 'react'
import type { ReviewComment } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { type Numbered, rangeLabel } from './model'

// What the comments of a file can ask of the pane, which keeps them.
export type CommentActions = {
  save: (body: string) => void // the new comment on the selection
  cancel: () => void // drop the selection
  edit: (index: number | null) => void // open or close the editor of a comment
  update: (index: number, body: string) => void
  remove: (index: number) => void
}

type BoxProps = {
  initial?: string
  label: string
  onSave: (body: string) => void
  onCancel: () => void
}

// The editor of one comment. Ctrl+Enter or Cmd+Enter saves, Escape cancels.
export function CommentBox({ initial = '', label, onSave, onCancel }: BoxProps) {
  const [body, setBody] = useState(initial)
  const save = () => body.trim() && onSave(body.trim())
  return (
    <div className="m-2 flex flex-col gap-2 rounded-lg border bg-background p-2 font-sans">
      <span className="text-xs text-muted-foreground">Comment on {label}</span>
      <Textarea
        autoFocus
        value={body}
        placeholder="Leave a comment"
        className="text-sm"
        onChange={(event) => setBody(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
            event.preventDefault()
            save()
          } else if (event.key === 'Escape') {
            event.preventDefault()
            event.stopPropagation()
            onCancel()
          }
        }}
      />
      <div className="flex justify-end gap-2">
        <Button variant="ghost" size="sm" onClick={onCancel}>
          Cancel
        </Button>
        <Button size="sm" disabled={!body.trim()} onClick={save}>
          Save
        </Button>
      </div>
    </div>
  )
}

type CardProps = { comment: ReviewComment; onEdit: () => void; onDelete: () => void }

export function CommentCard({ comment, onEdit, onDelete }: CardProps) {
  return (
    <div className="m-2 rounded-lg border bg-background p-2 font-sans">
      <div className="flex items-center gap-1">
        <span className="flex-1 text-xs text-muted-foreground">{rangeLabel(comment)}</span>
        <Button variant="ghost" size="icon-xs" aria-label="Edit comment" onClick={onEdit}>
          <Pencil />
        </Button>
        <Button variant="ghost" size="icon-xs" aria-label="Delete comment" onClick={onDelete}>
          <Trash2 />
        </Button>
      </div>
      <p className="text-sm whitespace-pre-wrap">{comment.body}</p>
    </div>
  )
}

type ThreadProps = {
  comments: Numbered[]
  editing: number | null
  actions: CommentActions
  newLabel: string | null // the label of the new comment written here, if one is
}

// The comments below one line, or below the file header, and the editor of a new one.
export function CommentThread({ comments, editing, actions, newLabel }: ThreadProps) {
  if (!comments.length && newLabel === null) return null
  return (
    <div className="border-y bg-muted/50">
      {comments.map(({ comment, index }) =>
        editing === index ? (
          <CommentBox
            key={index}
            initial={comment.body}
            label={rangeLabel(comment)}
            onSave={(body) => actions.update(index, body)}
            onCancel={() => actions.edit(null)}
          />
        ) : (
          <CommentCard
            key={index}
            comment={comment}
            onEdit={() => actions.edit(index)}
            onDelete={() => actions.remove(index)}
          />
        ),
      )}
      {newLabel !== null && <CommentBox label={newLabel} onSave={actions.save} onCancel={actions.cancel} />}
    </div>
  )
}
