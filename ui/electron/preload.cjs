const { contextBridge, ipcRenderer } = require('electron')

// The one thing the page needs from the shell: to hear about the double tap on Shift.
contextBridge.exposeInMainWorld('shell', {
  onPaletteToggle: (callback) => {
    const listener = () => callback()
    ipcRenderer.on('palette:toggle', listener)
    return () => ipcRenderer.removeListener('palette:toggle', listener)
  },
})
