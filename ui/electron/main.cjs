// The window of bytearmy. State and logic live in the Python core that starts this process.
const { app, BrowserWindow } = require('electron')
const fs = require('node:fs')
const path = require('node:path')

const coreUrl = process.env.BYTEARMY_URL
const token = process.env.BYTEARMY_TOKEN
const DOUBLE_TAP_MS = 400

// What the window stores by itself, such as the logins made in web panes, lives where
// the core says: inside the root, so every root has its own.
if (process.env.BYTEARMY_PROFILE) app.setPath('userData', process.env.BYTEARMY_PROFILE)

let win = null

// Errors of the window's page and crashes of the processes behind it go to the log file the
// core names, with the time; without one they would show only on the terminal, if at all.
const windowLog = process.env.BYTEARMY_WINDOW_LOG
// Past this size the log moves to <log>.1, replacing the one there: the log keeps at most
// twice this much.
const WINDOW_LOG_MAX_BYTES = 1024 * 1024

function logLine(text) {
  const line = `${new Date().toISOString()} ${text}`
  console.log(line)
  if (!windowLog) return
  try {
    fs.mkdirSync(path.dirname(windowLog), { recursive: true })
    if (fs.existsSync(windowLog) && fs.statSync(windowLog).size > WINDOW_LOG_MAX_BYTES) {
      fs.renameSync(windowLog, `${windowLog}.1`)
    }
    fs.appendFileSync(windowLog, `${line}\n`)
  } catch (error) {
    console.log(`cannot write ${windowLog}: ${error}`)
  }
}

process.on('uncaughtException', (error) => logLine(`[main] uncaught ${error.stack || error}`))
app.on('child-process-gone', (_event, details) => {
  logLine(`[process gone] ${details.type} ${details.reason}, exit code ${details.exitCode}`)
})

// The window's page logs its errors; web panes show pages from anywhere, whose errors are
// not ours, so of them only a crash or a hang is logged.
function watch(contents) {
  const kind = contents.getType()
  // URLs without their query: the window's carries the token.
  const where = () => contents.getURL().split('?')[0]
  contents.on('render-process-gone', (_event, details) => {
    logLine(`[${kind} gone] ${details.reason}, exit code ${details.exitCode}: ${where()}`)
  })
  contents.on('unresponsive', () => logLine(`[${kind} unresponsive] ${where()}`))
  contents.on('responsive', () => logLine(`[${kind} responsive again] ${where()}`))
  if (kind === 'webview') return
  contents.on('console-message', (event) => {
    if (event.level === 'error') logLine(`[page error] ${event.message} (${event.sourceId.split('?')[0]}:${event.lineNumber})`)
  })
  contents.on('preload-error', (_event, file, error) => logLine(`[preload error] ${file}: ${error.stack || error}`))
}

// A tap is Shift pressed and released with no other key in between; two taps within
// DOUBLE_TAP_MS toggle the command dialogue. Keys are watched here, in the main
// process, because an embedded web page does not pass them on to the window's page.
let shiftHeldAlone = false
let lastTapAt = 0

function onInput(input) {
  if (input.key !== 'Shift') {
    if (input.type === 'keyDown') {
      shiftHeldAlone = false
      lastTapAt = 0
    }
    return
  }
  if (input.type === 'keyDown') {
    if (!input.isAutoRepeat) shiftHeldAlone = true
    return
  }
  if (input.type !== 'keyUp' || !shiftHeldAlone) return
  shiftHeldAlone = false
  const now = Date.now()
  if (now - lastTapAt > DOUBLE_TAP_MS) {
    lastTapAt = now
    return
  }
  lastTapAt = 0
  togglePalette()
}

function togglePalette() {
  if (!win || win.isDestroyed()) return
  win.webContents.focus()
  win.webContents.send('palette:toggle')
}

app.on('web-contents-created', (_event, contents) => {
  watch(contents)
  contents.on('before-input-event', (_e, input) => onInput(input))
  // A web pane shows pages from anywhere: it gets no access to this process.
  contents.on('will-attach-webview', (_e, webPreferences) => {
    delete webPreferences.preload
    webPreferences.nodeIntegration = false
    webPreferences.contextIsolation = true
  })
  // A web pane opens no windows: the page is told what the window would have shown, and
  // opens it as a new pane.
  if (contents.getType() === 'webview') {
    contents.setWindowOpenHandler(({ url }) => {
      if (win && !win.isDestroyed()) win.webContents.send('webview:open', { from: contents.id, url })
      return { action: 'deny' }
    })
  }
})

app.whenReady().then(() => {
  win = new BrowserWindow({
    width: 1360,
    height: 840,
    title: 'bytearmy',
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      webviewTag: true, // web panes
    },
  })
  win.loadURL(`${coreUrl}/?token=${encodeURIComponent(token)}`)
  runSmokeHooks()
})

app.on('window-all-closed', () => app.quit())

// Smoke-test hooks, driven by environment variables: toggle the command dialogue, run a
// script in the page, send mouse input, save a screenshot, quit. Nothing happens unless
// BYTEARMY_SMOKE_QUIT_AFTER is set.
function runSmokeHooks() {
  const quitAfter = Number(process.env.BYTEARMY_SMOKE_QUIT_AFTER || 0)
  if (!quitAfter) return
  win.webContents.on('console-message', (event) => {
    if (event.level === 'error') console.log(`[renderer error] ${event.message}`)
  })
  // One line per page a web pane loads, or fails to load.
  app.on('web-contents-created', (_event, contents) => {
    if (contents.getType() !== 'webview') return
    contents.on('did-finish-load', () => console.log(`[webview] loaded ${contents.getURL()}`))
    contents.on('did-fail-load', (_e, code, description, url, isMainFrame) => {
      if (isMainFrame) console.log(`[webview] FAILED ${code} ${description} ${url}`)
    })
  })
  // BYTEARMY_SMOKE_INPUT: a JSON list of input events, each sent `at` seconds after start.
  for (const { at, ...event } of JSON.parse(process.env.BYTEARMY_SMOKE_INPUT || '[]')) {
    setTimeout(() => win.webContents.sendInputEvent(event), at * 1000)
  }
  for (const seconds of (process.env.BYTEARMY_SMOKE_PALETTE_AT || '').split(',').filter(Boolean)) {
    setTimeout(togglePalette, Number(seconds) * 1000)
  }
  const script = process.env.BYTEARMY_SMOKE_JS
  if (script) {
    win.webContents.once('did-finish-load', () => {
      setTimeout(() => {
        win.webContents
          .executeJavaScript(script, true)
          .then((result) => console.log(`[smoke] ${JSON.stringify(result)}`))
          .catch((error) => console.log(`[smoke error] ${error}`))
      }, 1500)
    })
  }
  setTimeout(async () => {
    const shot = process.env.BYTEARMY_SMOKE_SCREENSHOT
    if (shot) fs.writeFileSync(shot, (await win.webContents.capturePage()).toPNG())
    app.quit()
  }, quitAfter * 1000)
}
