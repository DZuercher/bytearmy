// What the parts of the diff pane share: where a line is, what is selected, and how the
// lines of a hunk pair up side by side.
import type { DiffFile, DiffLine, Hunk, ReviewComment } from '@/api/client'

export type Side = ReviewComment['side']

// A line a comment can be on: deleted lines count on the old side, all others on the new.
export type LineRef = { side: Side; line: number }

// The lines picked for a new comment. `anchor` is the line clicked first, so a shift-click
// can extend the range in either direction. start = end = 0 is the whole file.
export type Selection = { path: string; side: Side; start: number; end: number; anchor: number }

export type View = 'unified' | 'split'

// A comment together with its place in the list of all comments of the review.
export type Numbered = { comment: ReviewComment; index: number }

export function lineRef(line: DiffLine): LineRef {
  return line.kind === 'delete' ? { side: 'old', line: line.old ?? 0 } : { side: 'new', line: line.new ?? 0 }
}

export function sameRef(a: LineRef, b: LineRef): boolean {
  return a.side === b.side && a.line === b.line
}

export function inRange(ref: LineRef, range: { side: Side; start: number; end: number }): boolean {
  return range.start > 0 && ref.side === range.side && ref.line >= range.start && ref.line <= range.end
}

// The text of the lines in a range, as the agent sees it quoted.
export function quote(file: DiffFile, range: { side: Side; start: number; end: number }): string {
  if (range.start === 0) return ''
  return file.hunks
    .flatMap((hunk) => hunk.lines)
    .filter((line) => inRange(lineRef(line), range))
    .map((line) => line.text)
    .join('\n')
}

// The lines a comment can be on in this file, as "side:line".
export function refKeys(file: DiffFile): Set<string> {
  return new Set(file.hunks.flatMap((hunk) => hunk.lines.map((line) => refKey(lineRef(line)))))
}

export function refKey(ref: LineRef): string {
  return `${ref.side}:${ref.line}`
}

// "line 12" or "lines 12–15", with "old" for deleted lines; empty for the whole file.
export function rangeLabel(comment: { side: Side; start: number; end: number }): string {
  if (comment.start === 0) return 'file'
  const lines = comment.start === comment.end ? `line ${comment.start}` : `lines ${comment.start}–${comment.end}`
  return comment.side === 'old' ? `old ${lines}` : lines
}

// The rows of the split view: indices into `hunk.lines`, null where a side has no line.
// Deletions pair with the additions that follow them; context shows on both sides.
export type Pair = { left: number | null; right: number | null }

export function pairLines(hunk: Hunk): Pair[] {
  const pairs: Pair[] = []
  let deletes: number[] = []
  let adds: number[] = []
  const flush = () => {
    for (let i = 0; i < Math.max(deletes.length, adds.length); i++) {
      pairs.push({ left: deletes[i] ?? null, right: adds[i] ?? null })
    }
    deletes = []
    adds = []
  }
  hunk.lines.forEach((line, i) => {
    if (line.kind === 'context') {
      flush()
      pairs.push({ left: i, right: i })
    } else if (line.kind === 'delete') {
      if (adds.length) flush()
      deletes.push(i)
    } else {
      adds.push(i)
    }
  })
  flush()
  return pairs
}
