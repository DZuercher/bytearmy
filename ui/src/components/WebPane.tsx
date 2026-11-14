import { useEffect, useState } from 'react'
import { panePage } from '@/api/client'

type Props = {
  place: { project: string; task: string; pane: string }
}

// A browser view showing the page of one pane. The core says which page; for a tool with
// a service, such as the IDE, it answers once the service runs.
export function WebPane({ place }: Props) {
  const { project, task, pane } = place
  const [page, setPage] = useState<{ url?: string; error?: string }>({})

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

  if (page.error) return <p className="p-4 text-destructive">{page.error}</p>
  if (!page.url) return <p className="p-4 text-muted-foreground">Starting…</p>
  // All web panes share one session that is kept on disk: a login survives a restart.
  return <webview src={page.url} partition="persist:bytearmy" className="flex size-full" />
}
