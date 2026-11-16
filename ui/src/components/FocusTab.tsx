import { Focus } from 'lucide-react'
import { cn } from '@/lib/utils'

type Props = {
  open: boolean // whether the backlog bar below is open; closed, the tab shows only its icon
  active: boolean // focus mode is on
  onClick: () => void
}

// The small tab above the backlog bar that turns focus mode on and off: in focus mode
// every bar is collapsed and the inbox does not show how many messages are unread.
export function FocusTab({ open, active, onClick }: Props) {
  return (
    <div className={cn('shrink-0 border-b border-l', open ? 'w-56 px-4 py-3' : 'flex w-12 justify-center py-3')}>
      <button
        type="button"
        aria-pressed={active}
        aria-label="Focus mode"
        title={active ? 'Leave focus mode' : 'Focus mode: collapse all bars'}
        className={cn(
          'relative flex items-center gap-2 rounded-md outline-none focus-visible:ring-2',
          open ? 'w-full px-3 py-1.5' : 'size-8 justify-center',
          active
            ? 'bg-primary font-medium text-primary-foreground shadow-sm'
            : 'text-muted-foreground hover:bg-hover hover:text-foreground',
        )}
        onClick={onClick}
      >
        <Focus className="size-4 shrink-0" />
        {open && <span className="grow text-left">Focus mode</span>}
      </button>
    </div>
  )
}
