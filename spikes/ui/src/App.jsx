import { useEffect, useState } from 'react'
import { Mosaic, MosaicWindow } from 'react-mosaic-component'
import { getState, openSocket, rpc } from './api.js'
import { Palette } from './Palette.jsx'
import { TerminalPane } from './TerminalPane.jsx'
import { addLeaf, moveToOtherAxis, removeLeaf } from './tree.js'

const STATUS_COLORS = { amber: '#d97706', red: '#dc2626', green: '#16a34a', blue: '#2563eb' }

export function App() {
  const [mode, setMode] = useState(null)
  const [panes, setPanes] = useState({})
  const [tree, setTree] = useState(null)
  const [maximized, setMaximized] = useState(null)
  const [status, setStatus] = useState({ status: 'New', color: null })
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    let socket
    let cancelled = false

    function apply(event) {
      if (event.type === 'pane.opened') {
        setPanes((current) => ({ ...current, [event.pane.id]: event.pane }))
        setTree((current) => addLeaf(current, event.pane.id))
      } else if (event.type === 'pane.closed') {
        setTree((current) => removeLeaf(current, event.id))
        setPanes(({ [event.id]: _closed, ...rest }) => rest)
      } else if (event.type === 'pane.maximized') {
        setMaximized(event.id)
      } else if (event.type === 'task.status') {
        setStatus({ status: event.status, color: event.color })
      }
    }

    getState()
      .then((state) => {
        if (cancelled) return
        setMode(state.mode)
        setPanes(Object.fromEntries(state.panes.map((pane) => [pane.id, pane])))
        setTree(state.panes.reduce((current, pane) => addLeaf(current, pane.id), null))
        setMaximized(state.maximized)
        setStatus(state.status)
        socket = openSocket('/events')
        socket.onmessage = (message) => apply(JSON.parse(message.data))
      })
      .catch((reason) => setError(String(reason)))

    return () => {
      cancelled = true
      socket?.close()
    }
  }, [])

  useEffect(() => {
    window.shell?.onPaletteToggle(() => setPaletteOpen((open) => !open))
  }, [])

  const call = (method, params) => rpc(method, params).catch((reason) => setError(String(reason)))

  if (error) return <pre className="error">{error}</pre>
  if (mode === null) return null

  if (mode === 'a') {
    const [first] = Object.keys(panes)
    return <div className="single">{first && <TerminalPane paneId={first} />}</div>
  }

  return (
    <div className="app">
      <header>
        <strong>bytearmy spike {mode.toUpperCase()}</strong>
        <span>demo-project / demo-task</span>
        <span className="status" style={{ background: STATUS_COLORS[status.color] ?? status.color ?? '#64748b' }}>
          {status.status}
        </span>
        <span className="spacer" />
        <button onClick={() => setPaletteOpen(true)}>Palette (Shift Shift)</button>
      </header>

      {/* Maximize keeps every pane mounted: the tile is only stretched over the others. */}
      {maximized && (
        <style>{`.mosaic-tile:has([data-pane="${maximized}"]) {
          top: 0 !important; right: 0 !important; bottom: 0 !important; left: 0 !important; z-index: 20;
        }`}</style>
      )}

      <main>
        <Mosaic
          value={tree}
          onChange={setTree}
          zeroStateView={<div className="empty">No panes. Press Shift twice.</div>}
          renderTile={(id, path) => {
            const pane = panes[id]
            if (!pane) return <div />
            return (
              <MosaicWindow
                path={path}
                title={`${pane.title} (${pane.id})`}
                toolbarControls={[
                  <button key="move" title="Move to the other axis" onClick={() => setTree((current) => moveToOtherAxis(current, id))}>
                    Move
                  </button>,
                  <button
                    key="maximize"
                    title="Maximize / restore"
                    onClick={() => (maximized === id ? call('pane.restore') : call('pane.maximize', { id }))}
                  >
                    {maximized === id ? '❐' : '□'}
                  </button>,
                  <button key="close" title="Close" onClick={() => call('pane.close', { id })}>
                    ✕
                  </button>,
                ]}
              >
                <div className="pane-body" data-pane={pane.id}>
                  {pane.kind === 'terminal' ? (
                    <TerminalPane paneId={pane.id} />
                  ) : (
                    <webview className="web-host" src={pane.url} partition="persist:bytearmy-spike" />
                  )}
                </div>
              </MosaicWindow>
            )
          }}
        />
      </main>

      {paletteOpen && (
        <Palette
          onClose={() => setPaletteOpen(false)}
          onRun={(tool, arg) => {
            setPaletteOpen(false)
            call('pane.open', { tool, arg })
          }}
        />
      )}
    </div>
  )
}
