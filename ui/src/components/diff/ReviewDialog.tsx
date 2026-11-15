import { useState } from 'react'
import type { ReviewComment } from '@/api/client'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { rangeLabel } from './model'

type Props = {
  comments: ReviewComment[]
  summary: string
  recipient: string // who gets the review, e.g. "the master agent"
  onSummary: (summary: string) => void
  onClose: () => void
  onSubmit: () => Promise<unknown>
}

// Finishes a review: the user adds a summary if they like and sends all comments at once.
export function ReviewDialog({ comments, summary, recipient, onSummary, onClose, onSubmit }: Props) {
  const [error, setError] = useState<string | null>(null)
  const [sending, setSending] = useState(false)

  function submit() {
    setSending(true)
    setError(null)
    // On success the core closes the pane, and this dialog with it.
    onSubmit().catch((reason: unknown) => {
      setSending(false)
      setError(reason instanceof Error ? reason.message : String(reason))
    })
  }

  return (
    <Dialog open onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Finish review</DialogTitle>
          <DialogDescription>
            {comments.length} {comments.length === 1 ? 'comment goes' : 'comments go'} to {recipient}.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-2">
          <Label htmlFor="review-summary">Summary</Label>
          <Textarea
            id="review-summary"
            autoFocus
            value={summary}
            placeholder="Optional: what should happen next?"
            onChange={(event) => onSummary(event.target.value)}
          />
        </div>
        {comments.length > 0 && (
          <ul className="flex max-h-64 flex-col gap-2 overflow-y-auto">
            {comments.map((comment, i) => (
              <li key={i} className="rounded-lg border p-2">
                <div className="truncate font-mono text-xs text-muted-foreground">
                  {comment.path} · {rangeLabel(comment)}
                </div>
                <p className="line-clamp-3 text-sm whitespace-pre-wrap">{comment.body}</p>
              </li>
            ))}
          </ul>
        )}
        {error && <p className="text-sm text-destructive">{error}</p>}
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button disabled={sending || (!comments.length && !summary.trim())} onClick={submit}>
            Submit review
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
