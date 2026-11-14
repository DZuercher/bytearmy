import { FitAddon } from '@xterm/addon-fit'
import { Terminal } from '@xterm/xterm'
import { useEffect, useRef } from 'react'
import { openSocket } from './api.js'

// One xterm.js terminal wired to the PTY of a pane over a WebSocket.
export function TerminalPane({ paneId }) {
  const host = useRef(null)

  useEffect(() => {
    const term = new Terminal({
      cursorBlink: true,
      fontFamily: '"MesloLGS NF", "JetBrainsMono Nerd Font", Menlo, monospace',
      fontSize: 13,
      scrollback: 10000,
    })
    const fit = new FitAddon()
    term.loadAddon(fit)
    term.open(host.current)

    const socket = openSocket(`/pty/${paneId}`)
    socket.binaryType = 'arraybuffer'
    const send = (message) => {
      if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message))
    }
    const sendSize = () => send({ type: 'resize', rows: term.rows, cols: term.cols })
    const fitToHost = () => {
      // A hidden or zero-sized host has no dimensions to fit to.
      if (host.current?.offsetWidth && host.current?.offsetHeight) fit.fit()
    }

    socket.onopen = () => {
      fitToHost()
      sendSize()
      term.focus()
    }
    socket.onmessage = (event) => {
      if (typeof event.data === 'string') {
        if (JSON.parse(event.data).type === 'exit') term.write('\r\n[process exited]\r\n')
      } else {
        term.write(new Uint8Array(event.data))
      }
    }
    const onData = term.onData((data) => send({ type: 'input', data }))
    const onResize = term.onResize(sendSize)
    const observer = new ResizeObserver(fitToHost)
    observer.observe(host.current)

    return () => {
      observer.disconnect()
      onData.dispose()
      onResize.dispose()
      socket.close()
      term.dispose()
    }
  }, [paneId])

  return <div className="terminal-host" ref={host} />
}
