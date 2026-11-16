import { XIcon } from 'lucide-react'
import { type ReactNode, useId } from 'react'
import { Button } from '@/components/ui/button'

// A dialog that covers only the area it is placed in (the main area of the window), not the
// whole window: the bars beside it stay usable while it is open. Its parent must be positioned.
export function PaneDialog({
  title,
  description,
  onClose,
  children,
}: {
  title: ReactNode
  description: ReactNode
  onClose: () => void
  children: ReactNode
}) {
  const titleId = useId()
  const descriptionId = useId()
  return (
    <div className="absolute inset-0 z-50 flex items-center justify-center bg-black/10 p-4">
      <div
        role="dialog"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        className="relative grid max-h-full w-full max-w-sm gap-4 overflow-y-auto rounded-xl bg-popover p-4 text-sm text-popover-foreground shadow-lg ring-1 ring-foreground/10"
        onKeyDown={(event) => {
          if (event.key !== 'Escape') return
          event.stopPropagation()
          onClose()
        }}
      >
        <div className="flex flex-col gap-2">
          <h2 id={titleId} className="font-heading text-base leading-none font-medium">
            {title}
          </h2>
          <p id={descriptionId} className="text-sm text-muted-foreground">
            {description}
          </p>
        </div>
        {children}
        <Button variant="ghost" className="absolute top-2 right-2" size="icon-sm" aria-label="Close" onClick={onClose}>
          <XIcon />
        </Button>
      </div>
    </div>
  )
}
