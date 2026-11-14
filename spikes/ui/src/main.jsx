import '@xterm/xterm/css/xterm.css'
import 'react-mosaic-component/react-mosaic-component.css'
import './style.css'
import { createRoot } from 'react-dom/client'
import { App } from './App.jsx'

// No StrictMode: its double-mounted effects would open every terminal socket twice.
createRoot(document.getElementById('root')).render(<App />)
