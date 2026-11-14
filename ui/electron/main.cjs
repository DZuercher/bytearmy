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
  contents.on('before-input-event', (_e, input) => onInput(input))
  // A web pane shows pages from anywhere: it gets no access to this process.
  contents.on('will-attach-webview', (_e, webPreferences) => {
    delete webPreferences.preload
    webPreferences.nodeIntegration = false
    webPreferences.contextIsolation = true
  })
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
