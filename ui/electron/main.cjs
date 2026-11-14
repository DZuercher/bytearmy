// The window of bytearmy. State and logic live in the Python core that starts this process.
const { app, BrowserWindow } = require('electron')
const fs = require('node:fs')

const coreUrl = process.env.BYTEARMY_URL
const token = process.env.BYTEARMY_TOKEN

app.whenReady().then(() => {
  const win = new BrowserWindow({
    width: 1360,
    height: 840,
    title: 'bytearmy',
    webPreferences: { contextIsolation: true, nodeIntegration: false },
  })
  win.loadURL(`${coreUrl}/?token=${encodeURIComponent(token)}`)
  runSmokeHooks(win)
})

app.on('window-all-closed', () => app.quit())

// Smoke-test hooks, driven by environment variables: run a script in the page,
// save a screenshot, quit. Nothing happens unless BYTEARMY_SMOKE_QUIT_AFTER is set.
function runSmokeHooks(win) {
  const quitAfter = Number(process.env.BYTEARMY_SMOKE_QUIT_AFTER || 0)
  if (!quitAfter) return
  win.webContents.on('console-message', (event) => {
    if (event.level === 'error') console.log(`[renderer error] ${event.message}`)
  })
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
