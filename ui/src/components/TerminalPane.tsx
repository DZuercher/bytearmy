import '@xterm/xterm/css/xterm.css'
import { FitAddon } from '@xterm/addon-fit'
import { Terminal } from '@xterm/xterm'
import { useEffect, useRef } from 'react'
import { openTerminal } from '@/api/client'

const RESIZE_PAUSE_MS = 100 // a window drag resizes many times; the core hears the last size
const RECONNECT_MS = 1_000

type Props = {
  place: { project: string; task: string; pane: string }
  visible: boolean // false while the workspace of another task is shown
  onEnded: () => void // the program of the pane has ended
}

// A terminal emulator wired to the program of one pane.
export function TerminalPane({ place, visible, onEnded }: Props) {
  const host = useRef<HTMLDivElement>(null)
  const terminal = useRef<Terminal | null>(null)
  const ended = useRef(onEnded)
  const { project, task, pane } = place

  useEffect(() => {
    ended.current = onEnded
  })

  useEffect(() => {
    const term = new Terminal({
      cursorBlink: true,
      fontFamily: '"MesloLGS NF", "JetBrainsMono Nerd Font", Menlo, monospace',
      fontSize: 13,
      scrollback: 10_000,
    })
    const fitter = new FitAddon()
    term.loadAddon(fitter)
    term.open(host.current!)
    fitter.fit() // a new program starts at the size the pane really has
    terminal.current = term

    let socket: WebSocket
    let retry: ReturnType<typeof setTimeout> | undefined
    let stopped = false
    const send = (message: object) => {
      if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message))
    }
    const connect = (first: boolean) => {
      socket = openTerminal({ project, task, pane }, { rows: term.rows, cols: term.cols })
      socket.binaryType = 'arraybuffer'
      socket.onopen = () => {
        // The core sends what was printed so far again; it replaces the screen.
        if (!first) term.reset()
        // A program restored after a restart still has the size it had before.
        send({ type: 'resize', rows: term.rows, cols: term.cols })
        if (first) term.focus()
      }
      socket.onmessage = (event) => {
        if (typeof event.data === 'string') {
          stopped = true
          ended.current()
        } else term.write(new Uint8Array(event.data))
      }
      // The connection can drop while the computer sleeps; the program runs on.
      socket.onclose = (event) => {
        if (!stopped && event.code < 4000) retry = setTimeout(() => connect(false), RECONNECT_MS)
      }
    }
    connect(true)

    let pending: ReturnType<typeof setTimeout> | undefined
    const typed = term.onData((data) => send({ type: 'input', data }))
    const resized = term.onResize(({ rows, cols }) => {
      clearTimeout(pending)
      pending = setTimeout(() => send({ type: 'resize', rows, cols }), RESIZE_PAUSE_MS)
    })
    // A hidden pane has no size to fit to.
    const observer = new ResizeObserver(() => {
      if (host.current?.offsetWidth && host.current.offsetHeight) fitter.fit()
    })
    observer.observe(host.current!)

    return () => {
      stopped = true
      clearTimeout(retry)
      clearTimeout(pending)
      observer.disconnect()
      typed.dispose()
      resized.dispose()
      socket.close()
      term.dispose()
    }
  }, [project, task, pane])

  useEffect(() => {
    if (visible) terminal.current?.focus()
  }, [visible])

  return <div ref={host} className="size-full bg-black" />
}
