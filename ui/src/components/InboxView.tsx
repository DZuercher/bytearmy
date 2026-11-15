import { Archive, GitPullRequest, Mail, MailOpen, RefreshCw } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import type { Inbox, Message } from '@/api/client'
import { type Browser, WebControls } from '@/components/WebPane'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

type Props = {
  inbox: Inbox
  onRead: (message: string, unread: boolean) => void
  onDismiss: (message: string) => void
  onRefresh: () => void
}

const SOURCE_NAMES: Record<Message['source'], string> = { github: 'GitHub' }

// "5 min ago": how long ago a message arrived, in the coarsest unit that fits.
function ago(iso: string, now: number): string {
  const seconds = Math.max(0, (now - Date.parse(iso)) / 1000)
  if (Number.isNaN(seconds)) return ''
  if (seconds < 60) return 'just now'
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`
  return `${Math.floor(seconds / 86400)} d ago`
}

// "review_requested" → "review requested"
const words = (text: string) => text.replaceAll('_', ' ')

// The messages of all sources on the left, the one selected on the right.
export function InboxView({ inbox, onRead, onDismiss, onRefresh }: Props) {
  const [selected, setSelected] = useState<string | null>(null)
  const messages = inbox.messages.filter((message) => !message.dismissed)
  const shown = messages.find((message) => message.id === selected)
  const now = Date.now()

  function open(message: Message) {
    setSelected(message.id)
    if (message.unread) onRead(message.id, false)
  }

  const errors = Object.entries(inbox.errors)
  return (
    <div className="flex min-h-0 flex-1 border-t">
      <section aria-label="Messages" className="flex w-96 shrink-0 flex-col border-r">
        <div className="flex items-center gap-2 px-4 py-2 text-muted-foreground">
          <p className="grow text-xs">
            {inbox.last_checked ? `Checked ${ago(inbox.last_checked, now)}` : 'Not checked yet'}
          </p>
          <Button variant="ghost" size="icon-sm" aria-label="Check for new messages" onClick={onRefresh}>
            <RefreshCw />
          </Button>
        </div>
        {errors.map(([source, error]) => (
          <p key={source} className="mx-4 mb-2 rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive">
            {SOURCE_NAMES[source as Message['source']] ?? source}: {error}
          </p>
        ))}
        <ul className="min-h-0 flex-1 overflow-y-auto">
          {messages.map((message) => (
            <li key={message.id} className="group relative border-b">
              <button
                type="button"
                aria-current={message.id === selected}
                className={cn(
                  'flex w-full gap-3 px-4 py-3 text-left outline-none focus-visible:ring-2',
                  message.id === selected ? 'bg-hover' : 'hover:bg-hover',
                )}
                onClick={() => open(message)}
              >
                <span
                  aria-label={message.unread ? 'Unread' : undefined}
                  className={cn('mt-1.5 size-2 shrink-0 rounded-full', message.unread && 'bg-primary')}
                />
                <span className="min-w-0 grow">
                  <span className={cn('line-clamp-2', message.unread && 'font-semibold')}>{message.title}</span>
                  <span className="mt-1 flex items-center gap-1.5 text-xs text-muted-foreground">
                    <GitPullRequest className="size-3 shrink-0" aria-label={SOURCE_NAMES[message.source]} />
                    <span className="truncate">{message.context}</span>
                    <span>·</span>
                    <span className="shrink-0">{words(message.reason)}</span>
                    <span className="ml-auto shrink-0">{ago(message.updated_at, now)}</span>
                  </span>
                </span>
              </button>
              <div className="absolute top-2 right-2 hidden gap-1 rounded-md bg-background shadow-sm group-focus-within:flex group-hover:flex">
                <Button
                  variant="ghost"
                  size="icon-xs"
                  aria-label={message.unread ? 'Mark read' : 'Mark unread'}
                  title={message.unread ? 'Mark read' : 'Mark unread'}
                  onClick={() => onRead(message.id, !message.unread)}
                >
                  {message.unread ? <MailOpen /> : <Mail />}
                </Button>
                <Button
                  variant="ghost"
                  size="icon-xs"
                  aria-label="Dismiss"
                  title="Dismiss"
                  onClick={() => {
                    if (message.id === selected) setSelected(null)
                    onDismiss(message.id)
                  }}
                >
                  <Archive />
                </Button>
              </div>
            </li>
          ))}
        </ul>
        {messages.length === 0 && <p className="px-4 py-3 text-muted-foreground">No messages.</p>}
      </section>
      <section aria-label="Message" className="flex min-w-0 flex-1 flex-col">
        {shown ? (
          <Reader key={shown.id} message={shown} />
        ) : (
          <p className="p-4 text-muted-foreground">Select a message to read it.</p>
        )}
      </section>
    </div>
  )
}

// The page of a message in a browser view. It shares the session of the web panes, so a
// GitHub login made there holds here too.
function Reader({ message }: { message: Message }) {
  const element = useRef<Browser | null>(null)
  const [browser, setBrowser] = useState<Browser>()

  useEffect(() => {
    const view = element.current
    if (!view) return
    const onReady = () => setBrowser(view)
    view.addEventListener('dom-ready', onReady, { once: true })
    return () => view.removeEventListener('dom-ready', onReady)
  }, [])

  return (
    <>
      <div className="flex items-center gap-1 border-b px-2 py-1">
        <WebControls browser={browser} />
        <p className="min-w-0 grow truncate px-2 text-muted-foreground">{message.url}</p>
      </div>
      {message.url ? (
        <webview
          ref={(view) => {
            element.current = view as Browser | null
          }}
          src={message.url}
          partition="persist:bytearmy"
          className="flex min-h-0 flex-1"
        />
      ) : (
        <p className="p-4 text-muted-foreground">This message has no page.</p>
      )}
    </>
  )
}
