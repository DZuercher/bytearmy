const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('shell', {
  onPaletteToggle: (callback) => ipcRenderer.on('palette:toggle', () => callback()),
})
