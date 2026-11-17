import { Bot } from 'lucide-react'
import { STATES } from '@/lib/states'
import { cn } from '@/lib/utils'

type Props = {
  open: boolean // whether the projects bar below is open; closed, the tab shows only its icon
  active: boolean // the orchestrator is shown
  running: boolean // its program runs
  needsInput: boolean // it waits for the user
  onClick: () => void
}

// The small tab above the inbox tab that brings the user to the orchestrator.
export function OrchestratorTab({ open, active, running, needsInput, onClick }: Props) {
  return (
    <div className={cn('shrink-0 border-r border-b', open ? 'w-full px-4 py-3' : 'flex w-12 justify-center py-3')}>
      <button
        type="button"
        aria-current={active}
        aria-label={
          !running ? 'Orchestrator, not running' : needsInput ? 'Orchestrator, needs input' : 'Orchestrator'
        }
        title={!running ? 'Orchestrator (not running)' : needsInput ? 'Orchestrator (needs input)' : 'Orchestrator'}
        className={cn(
          'relative flex items-center gap-2 rounded-md outline-none focus-visible:ring-2',
          open ? 'w-full px-3 py-1.5' : 'size-8 justify-center',
          !active && 'text-muted-foreground hover:bg-hover hover:text-foreground',
          // A task waiting for the user has the same amber.
          needsInput && cn('border-l-4 text-foreground', STATES.needs_input.color),
          // After the color, so that the shown tab stays dark; it keeps its amber left border.
          active && 'bg-primary font-medium text-primary-foreground shadow-sm',
        )}
        onClick={onClick}
      >
        <Bot className="size-4 shrink-0" />
        {open && <span className="grow text-left">Orchestrator</span>}
        {!running && (
          <span
            className={cn('size-2 rounded-full bg-destructive', !open && 'absolute -top-0.5 -right-0.5')}
            aria-hidden
          />
        )}
      </button>
    </div>
  )
}
