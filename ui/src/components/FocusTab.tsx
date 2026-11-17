import { Focus, Settings } from 'lucide-react'
import { cn } from '@/lib/utils'

type Props = {
  open: boolean // whether the backlog bar below is open; closed, the tab shows only its icons
  active: boolean // focus mode is on
  onClick: () => void
  onSettings: () => void
}

// The small tab above the backlog bar that turns focus mode on and off: in focus mode
// every bar is collapsed and the inbox does not show how many messages are unread.
// The settings button sits to its right, or below it while the bar is closed.
export function FocusTab({ open, active, onClick, onSettings }: Props) {
  return (
    <div
      className={cn(
        'flex shrink-0 gap-1 border-b border-l',
        open ? 'w-full px-4 py-3' : 'w-12 flex-col items-center py-3',
      )}
    >
      <button
        type="button"
        aria-pressed={active}
        aria-label="Focus mode"
        title={active ? 'Leave focus mode' : 'Focus mode: collapse all bars'}
        className={cn(
          'relative flex items-center gap-2 rounded-md outline-none focus-visible:ring-2',
          open ? 'grow px-3 py-1.5' : 'size-8 justify-center',
          active
            ? 'bg-primary font-medium text-primary-foreground shadow-sm'
            : 'text-muted-foreground hover:bg-hover hover:text-foreground',
        )}
        onClick={onClick}
      >
        <Focus className="size-4 shrink-0" />
        {open && <span className="grow text-left">Focus mode</span>}
      </button>
      <button
        type="button"
        aria-label="Settings"
        title="Settings"
        className={cn(
          'flex shrink-0 items-center justify-center rounded-md text-muted-foreground outline-none hover:bg-hover hover:text-foreground focus-visible:ring-2',
          open ? 'px-2' : 'size-8',
        )}
        onClick={onSettings}
      >
        <Settings className="size-4" />
      </button>
    </div>
  )
}
