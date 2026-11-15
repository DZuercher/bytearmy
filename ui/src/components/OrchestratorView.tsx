import { useEffect, useRef, useState } from 'react'
import { type Orchestrator, openOrchestratorTerminal } from '@/api/client'
import { TerminalPane } from '@/components/TerminalPane'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

type Props = {
  orchestrator: Orchestrator
  visible: boolean // false while the workspace or the inbox is shown; it stays mounted
  onEnded: () => void // its program ended: fetch the state
  onRestart: () => void
}

// The orchestrator's terminal, filling the middle of the window.
export function OrchestratorView({ orchestrator, visible, onEnded, onRestart }: Props) {
  // A new run gets a fresh terminal; the old one shows the end of the earlier run.
  const [run, setRun] = useState(0)
  const wasRunning = useRef(orchestrator.running)
  useEffect(() => {
    if (orchestrator.running && !wasRunning.current) setRun((count) => count + 1)
    wasRunning.current = orchestrator.running
  }, [orchestrator.running])
  const code = orchestrator.exit_code

  return (
    <div className={cn('flex min-h-0 flex-1 flex-col px-4 pb-4', !visible && 'hidden')}>
      {!orchestrator.running && (
        <div className="mb-2 flex items-center gap-3 rounded-md border px-3 py-2">
          <span className={cn('grow', code != null && 'text-destructive')}>
            {code != null ? `The orchestrator ended with code ${code}.` : 'The orchestrator has ended.'}
          </span>
          <Button size="sm" onClick={onRestart}>
            Start again
          </Button>
        </div>
      )}
      <div className="min-h-0 flex-1 overflow-hidden rounded-md border bg-black p-1">
        <TerminalPane
          program={`orchestrator/${run}`}
          connect={openOrchestratorTerminal}
          visible={visible}
          onEnded={onEnded}
        />
      </div>
    </div>
  )
}
