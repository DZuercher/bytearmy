const { contextBridge, ipcRenderer } = require('electron')

// What the page needs from the shell: to hear about the double tap on Shift, and about
// new windows that pages in web panes ask for.
function subscribe(channel, callback) {
  const listener = (_event, message) => callback(message)
  ipcRenderer.on(channel, listener)
  return () => ipcRenderer.removeListener(channel, listener)
}

contextBridge.exposeInMainWorld('shell', {
  onPaletteToggle: (callback) => subscribe('palette:toggle', () => callback()),
  onOpenWindow: (callback) => subscribe('webview:open', callback),
})
