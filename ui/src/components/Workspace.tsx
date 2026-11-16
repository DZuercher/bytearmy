import 'react-mosaic-component/react-mosaic-component.css'
import { EyeOff, Maximize2, Minimize2, Send, X } from 'lucide-react'
import { useRef, useState } from 'react'
import { Mosaic, type MosaicNode, MosaicWindow } from 'react-mosaic-component'
import { type AppState, type Project, type Task, type Tree, api, openTerminal } from '@/api/client'
import { TerminalPane } from '@/components/TerminalPane'
import { Button } from '@/components/ui/button'
import { type Browser, WebControls, WebPane } from '@/components/WebPane'
import { cn } from '@/lib/utils'

type Props = {
  project: Project
  task: Task
  visible: boolean // false while the workspace of another task is shown
  run: (call: Promise<AppState>) => void
  onArrange: (tree: Tree) => void // the user is dragging; show the layout at once
  onActivePane: (pane: string) => void
}

// The tiling workspace of one task. The core owns the layout; this draws it and reports
// what the user rearranges. Every pane stays mounted while its place changes.
export function Workspace({ project, task, visible, run, onArrange, onActivePane }: Props) {
  const { workspace } = task
  const key = `${project.slug}/${task.slug}`
  const place = { project: project.slug, task: task.slug }
  // The browser views of the web panes, for the buttons in their title bars.
  const [browsers, setBrowsers] = useState<Record<string, Browser>>({})
  // Whether the mouse went down on a part of a title bar that does not drag its pane.
  const pressedNoDrag = useRef(false)

  function setBrowser(pane: string, browser: Browser | null) {
    setBrowsers(({ [pane]: _old, ...others }) => (browser ? { ...others, [pane]: browser } : others))
  }

  return (
    <div
      data-workspace={key}
      className={cn('relative min-h-0 flex-1', !visible && 'hidden')}
      // A title bar drags its pane, but its title is text to select and its buttons are to
      // click: a drag that starts on them is cancelled before the drag source hears of it.
      onMouseDownCapture={(event) => {
        pressedNoDrag.current = !!(event.target as Element).closest('[data-no-drag]')
      }}
      onDragStartCapture={(event) => {
        if (!pressedNoDrag.current) return
        event.preventDefault()
        event.stopPropagation()
      }}
    >
      {/* Maximizing only stretches one tile over the others; nothing is unmounted. */}
      {workspace.maximized && (
        <style>{`[data-workspace="${key}"] .mosaic-tile:has([data-pane="${workspace.maximized}"]) {
          inset: 0 !important; z-index: 20;
        }`}</style>
      )}
      <Mosaic<string>
        value={toMosaic(workspace.tree)}
        onChange={(tree) => onArrange(fromMosaic(tree))}
        onRelease={(tree) => run(api.setLayout(place.project, place.task, fromMosaic(tree)))}
        zeroStateView={
          <p className="p-4 text-muted-foreground">No panes. Press Shift twice to open a tool.</p>
        }
        renderTile={(id, path) => {
          const pane = workspace.panes?.[id]
          if (!pane) return <div />
          const maximized = workspace.maximized === id
          const ended = pane.exit_code == null ? '' : ` (ended with code ${pane.exit_code})`
          return (
            <MosaicWindow<string>
              path={path}
              title={pane.title + ended}
              // An agent wants the user to look here; clicking into the pane ends it. The frame
              // is a layer above the pane, as its title bar and content would cover an outline.
              className={cn(
                maximized && 'pane-maximized',
                pane.highlighted &&
                  "relative after:pointer-events-none after:absolute after:inset-0 after:z-30 after:border-4 after:border-amber-500 after:content-['']",
              )}
              toolbarControls={[
                pane.kind !== 'terminal' && <WebControls key="web" browser={browsers[id]} />,
                pane.kind === 'diff' && (
                  <Button
                    key="finish"
                    variant="ghost"
                    size="icon-xs"
                    aria-label="Finish review"
                    title="Finish the review: send its open comments and close the pane"
                    onClick={() => run(api.finishReview(place.project, place.task, id))}
                  >
                    <Send />
                  </Button>
                ),
                <Button
                  key="maximize"
                  variant="ghost"
                  size="icon-xs"
                  aria-label={maximized ? 'Restore pane' : 'Maximize pane'}
                  onClick={() => run(api.maximizePane(place.project, place.task, maximized ? null : id))}
                >
                  {maximized ? <Minimize2 /> : <Maximize2 />}
                </Button>,
                <Button
                  key="hide"
                  variant="ghost"
                  size="icon-xs"
                  aria-label="Hide pane"
                  onClick={() => run(api.hidePane(place.project, place.task, id, true))}
                >
                  <EyeOff />
                </Button>,
                <Button
                  key="close"
                  variant="ghost"
                  size="icon-xs"
                  aria-label="Close pane"
                  onClick={() => run(api.closePane(place.project, place.task, id))}
                >
                  <X />
                </Button>,
              ]}
              // The library's title bar, but with the title as text to select and copy.
              renderToolbar={({ title, toolbarControls }) => (
                <div className="flex size-full min-w-0 items-center">
                  <div className="mosaic-window-title">
                    <span data-no-drag draggable={false} title={title} className="cursor-text truncate select-text">
                      {title}
                    </span>
                  </div>
                  <div data-no-drag className="mosaic-window-controls">
                    {toolbarControls}
                  </div>
                </div>
              )}
            >
              <div
                data-pane={id}
                className="size-full"
                onFocusCapture={() => onActivePane(id)}
                // A browser view tells only of `focus`, which React does not hear in a parent.
                ref={(element) => {
                  if (!element || !pane.highlighted) return
                  const seen = () => run(api.highlightPane(place.project, place.task, id, false))
                  element.addEventListener('focus', seen, { capture: true, once: true })
                  return () => element.removeEventListener('focus', seen, { capture: true })
                }}
              >
                {pane.kind === 'web' || pane.kind === 'diff' || pane.kind === 'answer' ? (
                  <WebPane
                    place={{ ...place, pane: id }}
                    onBrowser={(browser) => setBrowser(id, browser)}
                    // A link that wants a new window opens as a new web pane beside this one.
                    onOpenWindow={(url) =>
                      run(
                        api.openPane(place.project, place.task, {
                          tool: 'webpage',
                          values: { url },
                          beside: id,
                          hidden: false,
                        }),
                      )
                    }
                  />
                ) : (
                  <TerminalPane
                    program={`${place.project}/${place.task}/${id}`}
                    connect={(size) => openTerminal({ ...place, pane: id }, size)}
                    visible={visible}
                    onEnded={() => run(api.state())}
                  />
                )}
              </div>
            </MosaicWindow>
          )
        }}
      />
    </div>
  )
}

// react-mosaic leaves out what the core sends as null, and the other way round.
function toMosaic(tree: Tree | undefined): MosaicNode<string> | null {
  if (tree == null || typeof tree === 'string') return tree ?? null
  return {
    type: 'split',
    direction: tree.direction,
    children: tree.children.map((child) => toMosaic(child)!),
    splitPercentages: tree.splitPercentages ?? undefined,
  }
}

function fromMosaic(node: MosaicNode<string> | null): Tree {
  if (node === null || typeof node === 'string') return node
  if (node.type === 'tabs') {
    // bytearmy has no tabbed panes; should one appear, its panes go side by side.
    return { type: 'split', direction: 'row', children: node.tabs }
  }
  return {
    type: 'split',
    direction: node.direction,
    children: node.children.map((child) => fromMosaic(child)!),
    splitPercentages: node.splitPercentages ?? null,
  }
}
