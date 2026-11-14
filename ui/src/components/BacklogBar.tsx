import { ChevronLeft, ChevronRight } from 'lucide-react'
import { BarHeading } from '@/components/BarHeading'
import { Button } from '@/components/ui/button'

// Right-hand bar. It only takes its place in the layout so far; backlog tasks do not exist yet.
export function BacklogBar({ open, onToggle }: { open: boolean; onToggle: () => void }) {
  if (!open) {
    return (
      <nav aria-label="Backlog tasks (collapsed)" className="flex w-12 shrink-0 flex-col items-center gap-3 border-l py-3">
        <Button variant="ghost" size="icon-sm" aria-label="Expand backlog bar" onClick={onToggle}>
          <ChevronLeft />
        </Button>
        <BarHeading vertical>Backlog tasks</BarHeading>
      </nav>
    )
  }

  return (
    <nav aria-label="Backlog tasks" className="flex w-56 shrink-0 flex-col gap-2 border-l p-4">
      <div className="flex items-center gap-1">
        <Button variant="ghost" size="icon-sm" aria-label="Collapse backlog bar" onClick={onToggle}>
          <ChevronRight />
        </Button>
        <BarHeading>Backlog tasks</BarHeading>
      </div>
    </nav>
  )
}
