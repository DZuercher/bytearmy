import { MessagesSquare } from 'lucide-react'
import { cn } from '@/lib/utils'

type Props = {
  open: boolean // whether the projects bar below is open; closed, the tab shows only its icon
  active: boolean // the messengers are shown
  onClick: () => void
}

// The small tab below the inbox tab that brings the user to the messengers.
export function MessengersTab({ open, active, onClick }: Props) {
  return (
    <div className={cn('shrink-0 border-r border-b', open ? 'w-full px-4 py-3' : 'flex w-12 justify-center py-3')}>
      <button
        type="button"
        aria-current={active}
        aria-label="Messengers"
        title="Messengers"
        className={cn(
          'relative flex items-center gap-2 rounded-md outline-none focus-visible:ring-2',
          open ? 'w-full px-3 py-1.5' : 'size-8 justify-center',
          active
            ? 'bg-primary font-medium text-primary-foreground shadow-sm'
            : 'text-muted-foreground hover:bg-hover hover:text-foreground',
        )}
        onClick={onClick}
      >
        <MessagesSquare className="size-4 shrink-0" />
        {open && <span className="grow text-left">Messengers</span>}
      </button>
    </div>
  )
}
