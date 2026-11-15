import type { DiffFile } from '@/api/client'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

const STATUS = {
  added: { letter: 'A', className: 'text-task-green-mark' },
  modified: { letter: 'M', className: 'text-task-amber-mark' },
  deleted: { letter: 'D', className: 'text-task-red-mark' },
  renamed: { letter: 'R', className: 'text-task-blue-mark' },
} as const

export function StatusLetter({ status }: { status: DiffFile['status'] }) {
  const { letter, className } = STATUS[status]
  return (
    <span title={status} className={cn('w-3 shrink-0 font-mono text-xs font-semibold', className)}>
      {letter}
    </span>
  )
}

type Props = {
  files: DiffFile[]
  counts: Record<string, number> // the comments per file path
  onPick: (path: string) => void
}

// The changed files; picking one scrolls to it.
export function FileList({ files, counts, onPick }: Props) {
  return (
    <ul className="flex flex-col py-1">
      {files.map((file) => {
        const slash = file.path.lastIndexOf('/')
        return (
          <li key={file.path}>
            <button
              type="button"
              title={file.old_path ? `${file.old_path} → ${file.path}` : file.path}
              className="flex w-full items-center gap-2 px-2 py-0.5 text-left text-xs hover:bg-hover"
              onClick={() => onPick(file.path)}
            >
              <StatusLetter status={file.status} />
              <span className="min-w-0 flex-1 truncate">
                <span className="text-muted-foreground">{file.path.slice(0, slash + 1)}</span>
                <span className="font-semibold">{file.path.slice(slash + 1)}</span>
              </span>
              {counts[file.path] > 0 && <Badge variant="secondary">{counts[file.path]}</Badge>}
              <span className="font-mono text-task-green-mark">+{file.additions}</span>
              <span className="font-mono text-task-red-mark">−{file.deletions}</span>
            </button>
          </li>
        )
      })}
    </ul>
  )
}
