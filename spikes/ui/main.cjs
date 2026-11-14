// Throwaway spike shell: one window that shows the UI served by the Python core.
const { app, BrowserWindow } = require('electron')
const fs = require('node:fs')
const path = require('node:path')

const coreUrl = process.env.BYTEARMY_URL
const token = process.env.BYTEARMY_TOKEN
const DOUBLE_TAP_MS = 400

let win = null

// A "tap" is Shift pressed and released with no other key in between.
// Two taps within DOUBLE_TAP_MS toggle the palette. Key events are observed in
// the main process because a <webview> does not pass them on to the host page.
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
  win.webContents.focus() // take focus back from a <webview> so the palette can type
  win.webContents.send('palette:toggle')
}

app.on('web-contents-created', (_event, contents) => {
  contents.on('before-input-event', (_e, input) => onInput(input))
  contents.on('will-attach-webview', (_e, webPreferences) => {
    delete webPreferences.preload
    webPreferences.nodeIntegration = false
    webPreferences.contextIsolation = true
  })
  // One line per page load, so a blocked or failed embed is visible in the log.
  contents.on('did-finish-load', () => {
    console.log(`[${contents.getType()}] loaded ${contents.getURL().split('?')[0]}`)
  })
  contents.on('did-fail-load', (_e, code, description, url, isMainFrame) => {
    if (isMainFrame) console.log(`[${contents.getType()}] FAILED ${code} ${description} ${url.split('?')[0]}`)
  })
})

app.whenReady().then(() => {
  win = new BrowserWindow({
    width: 1500,
    height: 950,
    title: `bytearmy spike ${(process.env.BYTEARMY_SPIKE || '').toUpperCase()}`,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      webviewTag: true,
    },
  })
  win.webContents.on('console-message', (event) => {
    if (event.level === 'error' || event.level === 'warning') {
      console.log(`[renderer ${event.level}] ${event.message}`)
    }
  })
  win.loadURL(`${coreUrl}/?token=${encodeURIComponent(token)}`)

  // Smoke-test hooks: open the palette, save a screenshot, quit.
  const quitAfter = Number(process.env.BYTEARMY_SPIKE_AUTOQUIT || 0)
  if (quitAfter > 0) {
    if (process.env.BYTEARMY_SPIKE_AUTOPALETTE) {
      setTimeout(togglePalette, Math.max(0, quitAfter - 2) * 1000)
    }
    setTimeout(async () => {
      const shot = process.env.BYTEARMY_SPIKE_SCREENSHOT
      if (shot) fs.writeFileSync(shot, (await win.webContents.capturePage()).toPNG())
      app.quit()
    }, quitAfter * 1000)
  }
})

app.on('window-all-closed', () => app.quit())
