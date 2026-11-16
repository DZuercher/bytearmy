import { type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent, useState } from 'react'
import { cn } from '@/lib/utils'

export const BAR_WIDTH = 224 // the width a bar starts with; the core keeps the same default
export const MIN_BAR_WIDTH = 160 // the limits the core accepts
export const MAX_BAR_WIDTH = 640

type Props = {
  label: string // names the bar, e.g. "Resize tasks bar"
  side: 'left' | 'right' // the edge of the bar the handle sits on
  width: number
  onResize: (width: number) => void // while the edge is dragged
  onCommit: (width: number) => void // once it is let go, or reset with a double click
}

// The edge of a bar the user drags to change its width. It sits inside a bar whose
// parent is `relative`. A double click resets the width to the default.
export function ResizeHandle({ label, side, width, onResize, onCommit }: Props) {
  const [dragging, setDragging] = useState(false)

  function start(event: ReactPointerEvent) {
    if (event.button !== 0) return
    event.preventDefault()
    const startX = event.clientX
    const startWidth = width
    // The bar grows toward the middle of the window: rightward for a bar on the left.
    const direction = side === 'right' ? 1 : -1
    let latest = width
    setDragging(true)

    const move = (moved: PointerEvent) => {
      latest = clamp(startWidth + direction * (moved.clientX - startX))
      onResize(latest)
    }
    const end = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', end)
      window.removeEventListener('pointercancel', end)
      setDragging(false)
      if (latest !== startWidth) onCommit(latest)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', end)
    window.addEventListener('pointercancel', end)
  }

  function step(event: ReactKeyboardEvent) {
    const delta = ({ ArrowLeft: -16, ArrowRight: 16 } as Record<string, number>)[event.key]
    if (delta === undefined) return
    event.preventDefault()
    onCommit(clamp(width + (side === 'right' ? delta : -delta)))
  }

  return (
    <>
      <div
        role="separator"
        aria-label={label}
        aria-orientation="vertical"
        aria-valuenow={width}
        aria-valuemin={MIN_BAR_WIDTH}
        aria-valuemax={MAX_BAR_WIDTH}
        tabIndex={0}
        title="Drag to resize, double-click to reset"
        className={cn(
          'absolute inset-y-0 z-20 w-1.5 cursor-col-resize outline-none hover:bg-primary/40 focus-visible:bg-primary/40',
          side === 'right' ? '-right-0.75' : '-left-0.75',
          dragging && 'bg-primary/40',
        )}
        onPointerDown={start}
        onDoubleClick={() => onCommit(BAR_WIDTH)}
        onKeyDown={step}
      />
      {/* While dragging, web panes and terminals must not take the pointer away. */}
      {dragging && <div className="fixed inset-0 z-50 cursor-col-resize" />}
    </>
  )
}

function clamp(width: number) {
  return Math.round(Math.min(MAX_BAR_WIDTH, Math.max(MIN_BAR_WIDTH, width)))
}
