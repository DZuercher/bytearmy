import { ArrowLeft, ArrowRight, RotateCw } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { panePage, panePageVersion } from '@/api/client'
import { Button } from '@/components/ui/button'

const CHANGE_CHECK_MS = 1_000 // how often a pane that shows a file of the task looks for a change

// The parts of Electron's <webview> element that bytearmy uses. Its methods only work
// once the element has reported `dom-ready`.
export type Browser = HTMLElement & {
  canGoBack(): boolean
  canGoForward(): boolean
  goBack(): void
  goForward(): void
  reload(): void
  getWebContentsId(): number
}

type Props = {
  place: { project: string; task: string; pane: string }
  onBrowser: (browser: Browser | null) => void // the browser view, once it can be used
  onOpenWindow: (url: string) => void // the page asked for a new window
}

// A browser view showing the page of one pane. The core says which page; for a tool with
// a service, such as the IDE, it answers once the service runs.
export function WebPane({ place, onBrowser, onOpenWindow }: Props) {
  const { project, task, pane } = place
  const [page, setPage] = useState<{ url?: string; error?: string }>({})
  const browser = useRef<Browser | null>(null)
  const ready = useRef(false)
  const callbacks = useRef({ onBrowser, onOpenWindow })

  useEffect(() => {
    callbacks.current = { onBrowser, onOpenWindow }
  })

  useEffect(() => {
    let current = true
    panePage({ project, task, pane }).then(
      (url) => current && setPage({ url }),
      (reason: unknown) =>
        current && setPage({ error: reason instanceof Error ? reason.message : String(reason) }),
    )
    return () => {
      current = false
    }
  }, [project, task, pane])

  // Tell the workspace once the browser view can be steered, and hear about new windows.
  useEffect(() => {
    const element = browser.current
    if (!page.url || !element) return
    const onReady = () => {
      ready.current = true
      callbacks.current.onBrowser(element)
    }
    element.addEventListener('dom-ready', onReady, { once: true })
    // The shell turns every new window a page asks for into this message.
    const stopHearing = window.shell?.onOpenWindow(({ from, url }) => {
      if (ready.current && element.getWebContentsId() === from) callbacks.current.onOpenWindow(url)
    })
    return () => {
      element.removeEventListener('dom-ready', onReady)
      stopHearing?.()
      ready.current = false
      callbacks.current.onBrowser(null)
    }
  }, [page.url])

  // Load the page again when a file it shows has changed. Panes that show no file stop asking.
  useEffect(() => {
    let last: string | null | undefined
    const timer = setInterval(() => {
      panePageVersion({ project, task, pane }).then(
        (version) => {
          if (last !== undefined && version !== last && ready.current) browser.current?.reload()
          last = version
          if (version === null) clearInterval(timer)
        },
        () => undefined, // asked again in a moment
      )
    }, CHANGE_CHECK_MS)
    return () => clearInterval(timer)
  }, [project, task, pane])

  if (page.error) return <p className="p-4 text-destructive">{page.error}</p>
  if (!page.url) return <p className="p-4 text-muted-foreground">Starting…</p>
  // All web panes share one session that is kept on disk: a login survives a restart.
  return (
    <webview
      ref={(element) => {
        browser.current = element as Browser | null
      }}
      src={page.url}
      partition="persist:bytearmy"
      // Lets a page's request for a new window through to the shell, which opens no
      // window but has the page opened as a new pane. Without it the request just fails.
      // React drops the attribute when given `true`, so it gets the text Electron looks for.
      allowpopups={'true' as unknown as boolean}
      className="flex size-full"
    />
  )
}

// Back, forward and reload for the title bar of a web pane. Without a browser view that
// can be steered yet, the buttons are off.
export function WebControls({ browser }: { browser?: Browser }) {
  const [, setNavigations] = useState(0)

  useEffect(() => {
    if (!browser) return
    const navigated = () => setNavigations((count) => count + 1) // the history has changed
    const events = ['did-navigate', 'did-navigate-in-page']
    for (const event of events) browser.addEventListener(event, navigated)
    return () => {
      for (const event of events) browser.removeEventListener(event, navigated)
    }
  }, [browser])

  return (
    <>
      <Button
        variant="ghost"
        size="icon-xs"
        aria-label="Back"
        disabled={!browser?.canGoBack()}
        onClick={() => browser?.goBack()}
      >
        <ArrowLeft />
      </Button>
      <Button
        variant="ghost"
        size="icon-xs"
        aria-label="Forward"
        disabled={!browser?.canGoForward()}
        onClick={() => browser?.goForward()}
      >
        <ArrowRight />
      </Button>
      <Button
        variant="ghost"
        size="icon-xs"
        aria-label="Reload"
        disabled={!browser}
        onClick={() => browser?.reload()}
      >
        <RotateCw />
      </Button>
    </>
  )
}
