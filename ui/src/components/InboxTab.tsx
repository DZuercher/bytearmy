import { Inbox } from 'lucide-react'
import { cn } from '@/lib/utils'

type Props = {
  open: boolean // whether the projects bar below is open; closed, the tab shows only its icon
  active: boolean // the inbox is shown
  unread: number
  onClick: () => void
}

// The small tab above the projects bar that brings the user to the inbox.
export function InboxTab({ open, active, unread, onClick }: Props) {
  const count = unread > 99 ? '99+' : String(unread)
  return (
    <div className={cn('shrink-0 border-r border-b', open ? 'w-full px-4 py-3' : 'flex w-12 justify-center py-3')}>
      <button
        type="button"
        aria-current={active}
        aria-label={unread ? `Inbox, ${unread} unread` : 'Inbox'}
        title="Inbox"
        className={cn(
          'relative flex items-center gap-2 rounded-md outline-none focus-visible:ring-2',
          open ? 'w-full px-3 py-1.5' : 'size-8 justify-center',
          active
            ? 'bg-primary font-medium text-primary-foreground shadow-sm'
            : 'text-muted-foreground hover:bg-hover hover:text-foreground',
        )}
        onClick={onClick}
      >
        <Inbox className="size-4 shrink-0" />
        {open && <span className="grow text-left">Inbox</span>}
        {unread > 0 && (
          <span
            className={cn(
              'rounded-full bg-destructive px-1.5 text-xs leading-5 font-semibold text-white tabular-nums',
              !open && 'absolute -top-1.5 -right-1.5 px-1 text-[10px] leading-4',
            )}
          >
            {count}
          </span>
        )}
      </button>
    </div>
  )
}
