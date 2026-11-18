import { Component, type ErrorInfo, type ReactNode } from 'react'
import { Button } from '@/components/ui/button'

// An error while React draws the page would otherwise remove the whole page and leave the
// window white. This shows the error instead, with a way back, and logs it with its stack:
// the window's errors end up in electron.log.
export class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(`page failed: ${error.stack || error}\ncomponents:${info.componentStack ?? ''}`)
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children
    return (
      <div className="flex h-screen flex-col gap-4 overflow-auto bg-background p-6 text-foreground">
        <h1 className="text-lg font-semibold">bytearmy failed to draw the page</h1>
        <pre className="text-xs whitespace-pre-wrap text-muted-foreground">{error.stack || String(error)}</pre>
        <div className="flex gap-2">
          <Button onClick={() => this.setState({ error: null })}>Try again</Button>
          <Button variant="outline" onClick={() => window.location.reload()}>
            Reload
          </Button>
        </div>
      </div>
    )
  }
}
