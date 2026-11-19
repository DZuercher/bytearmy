import { cn } from '@/lib/utils'

const SLACK_URL = 'https://app.slack.com'

type Props = {
  visible: boolean // false while another view is shown; it stays mounted
}

// Slack in a browser view, filling the middle of the window. Its session is its own and kept
// on disk: a login survives a restart. The shell lets its pages open real windows.
export function MessengersView({ visible }: Props) {
  return (
    <div className={cn('flex min-h-0 flex-1 flex-col px-4 pb-4', !visible && 'hidden')}>
      <div className="min-h-0 flex-1 overflow-hidden rounded-md border">
        <webview
          src={SLACK_URL}
          partition="persist:messengers"
          // Without it a page's request for a window just fails. React drops the attribute
          // when given `true`, so it gets the text Electron looks for.
          allowpopups={'true' as unknown as boolean}
          className="flex size-full"
        />
      </div>
    </div>
  )
}
