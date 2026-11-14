import { cn } from '@/lib/utils'

// The small upper-case label that names a bar, as in ui.html.
export function BarHeading({ children, vertical = false }: { children: string; vertical?: boolean }) {
  return (
    <h2
      className={cn(
        'text-xs font-semibold tracking-widest text-muted-foreground uppercase',
        vertical && '[writing-mode:vertical-rl]',
      )}
    >
      {children}
    </h2>
  )
}
