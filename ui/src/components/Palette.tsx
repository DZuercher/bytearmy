import { type KeyboardEvent, useEffect, useRef, useState } from 'react'
import type { Tool } from '@/api/client'
import { cn } from '@/lib/utils'

type Props = {
  tools: Tool[]
  onRun: (tool: string, values: Record<string, string>) => void
  onClose: () => void
}

// The command dialogue: type to filter, arrows to move, Enter to run, Esc to close.
// A tool with parameters asks for them one after the other in the same field, which
// starts out with what the core offers for the parameter.
export function Palette({ tools, onRun, onClose }: Props) {
  const [text, setText] = useState('')
  const [selected, setSelected] = useState(0)
  const [asking, setAsking] = useState<{ tool: Tool; values: Record<string, string> } | null>(null)
  const input = useRef<HTMLInputElement>(null)

  useEffect(() => {
    input.current?.focus()
    input.current?.select() // typing replaces what is offered
  }, [asking])

  const matches = tools.filter(
    (tool) =>
      (tool.callers ?? ['user']).includes('user') && // some tools are for agents only
      tool.name.toLowerCase().includes(text.toLowerCase()),
  )
  const parameter = asking?.tool.parameters?.[Object.keys(asking.values).length]

  function choose(tool: Tool | undefined) {
    if (!tool) return
    if (tool.parameters?.length) {
      setAsking({ tool, values: {} })
      setText(tool.parameters[0].default ?? '')
    } else {
      onRun(tool.id, {})
    }
  }

  function answer() {
    if (!asking || !parameter || text.trim() === '') return
    const values = { ...asking.values, [parameter.name]: text }
    if (Object.keys(values).length === asking.tool.parameters?.length) {
      onRun(asking.tool.id, values)
    } else {
      setAsking({ tool: asking.tool, values })
      setText(asking.tool.parameters?.[Object.keys(values).length]?.default ?? '')
    }
  }

  function onKeyDown(event: KeyboardEvent) {
    if (event.key === 'Escape') onClose()
    else if (event.key === 'Enter') asking ? answer() : choose(matches[selected])
    else if (event.key === 'ArrowDown') setSelected((index) => Math.min(index + 1, matches.length - 1))
    else if (event.key === 'ArrowUp') setSelected((index) => Math.max(index - 1, 0))
    else return
    event.preventDefault()
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/30 pt-[18vh]"
      onMouseDown={onClose}
    >
      <div
        role="dialog"
        aria-label="Open a tool"
        className="w-[32rem] overflow-hidden rounded-lg bg-popover shadow-2xl ring-1 ring-border"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <input
          ref={input}
          value={text}
          placeholder={parameter ? parameter.label || parameter.name : 'Open a tool…'}
          className="w-full border-b px-4 py-3 text-base outline-none"
          onChange={(event) => {
            setText(event.target.value)
            setSelected(0)
          }}
          onKeyDown={onKeyDown}
        />
        {asking ? (
          <p className="px-4 py-2 text-muted-foreground">
            {asking.tool.name}: Enter to continue, Esc to cancel
          </p>
        ) : (
          <ul className="p-1">
            {matches.map((tool, index) => (
              <li
                key={tool.id}
                className={cn('cursor-pointer rounded-md px-3 py-2', index === selected && 'bg-accent')}
                onMouseEnter={() => setSelected(index)}
                onClick={() => choose(tool)}
              >
                {tool.name}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
