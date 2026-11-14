import { ChevronDown, ChevronUp } from 'lucide-react'
import { BarHeading } from '@/components/BarHeading'
import { Button } from '@/components/ui/button'

// Bottom bar. It only takes its place in the layout so far; tasks do not exist yet.
export function TaskBar({ open, onToggle }: { open: boolean; onToggle: () => void }) {
  return (
    <footer className={`flex shrink-0 items-center gap-4 border-t px-4 ${open ? 'h-16' : 'h-9'}`}>
      <BarHeading>Tasks</BarHeading>
      <div className="grow" />
      <Button
        variant="ghost"
        size="icon-sm"
        aria-label={open ? 'Collapse tasks bar' : 'Expand tasks bar'}
        onClick={onToggle}
      >
        {open ? <ChevronDown /> : <ChevronUp />}
      </Button>
    </footer>
  )
}
