import { useEffect, useRef, useState } from 'react'

const TOOLS = [
  { tool: 'terminal', label: 'Terminal' },
  { tool: 'claude', label: 'Claude' },
  { tool: 'webpage', label: 'Webpage', argLabel: 'URL', argDefault: 'https://github.com' },
  { tool: 'ide', label: 'IDE (VS Code)' },
]

// dmenu-like tool picker: type to filter, arrows to move, Enter to run, Esc to close.
export function Palette({ onRun, onClose }) {
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState(0)
  const [asking, setAsking] = useState(null) // the tool whose argument is being typed
  const input = useRef(null)

  useEffect(() => input.current?.focus(), [asking])

  const matches = TOOLS.filter((entry) => entry.label.toLowerCase().includes(query.toLowerCase()))

  function choose(entry) {
    if (!entry) return
    if (entry.argLabel) {
      setAsking(entry)
      setQuery(entry.argDefault)
    } else {
      onRun(entry.tool)
    }
  }

  function onKeyDown(event) {
    if (event.key === 'Escape') onClose()
    else if (asking && event.key === 'Enter') onRun(asking.tool, query)
    else if (event.key === 'Enter') choose(matches[selected])
    else if (event.key === 'ArrowDown') setSelected((index) => Math.min(index + 1, matches.length - 1))
    else if (event.key === 'ArrowUp') setSelected((index) => Math.max(index - 1, 0))
    else return
    event.preventDefault()
  }

  return (
    <div className="palette-backdrop" onMouseDown={onClose}>
      <div className="palette" onMouseDown={(event) => event.stopPropagation()}>
        <input
          ref={input}
          value={query}
          placeholder={asking ? asking.argLabel : 'Open a tool…'}
          onChange={(event) => {
            setQuery(event.target.value)
            setSelected(0)
          }}
          onKeyDown={onKeyDown}
        />
        {asking ? (
          <div className="palette-hint">{asking.label}: Enter to open, Esc to cancel</div>
        ) : (
          <ul>
            {matches.map((entry, index) => (
              <li
                key={entry.tool}
                className={index === selected ? 'selected' : ''}
                onMouseEnter={() => setSelected(index)}
                onClick={() => choose(entry)}
              >
                {entry.label}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
