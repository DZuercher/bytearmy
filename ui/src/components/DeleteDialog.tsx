import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'

export type Doomed = { kind: 'project' | 'task'; slug: string; name: string; path: string }

const CONTENTS = {
  project: 'with every task in it',
  task: 'with everything in it, including its clone',
}

// Asks before a project or a task is deleted. `doomed` is what to delete, or null when closed.
export function DeleteDialog({
  doomed,
  onCancel,
  onConfirm,
}: {
  doomed: Doomed | null
  onCancel: () => void
  onConfirm: (doomed: Doomed) => void
}) {
  return (
    <AlertDialog open={doomed !== null} onOpenChange={(open) => !open && onCancel()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>
            Delete {doomed?.kind} “{doomed?.name}”?
          </AlertDialogTitle>
          <AlertDialogDescription>
            All data associated with this {doomed?.kind} will be deleted: the directory{' '}
            <code className="break-all">{doomed?.path}</code> {doomed && CONTENTS[doomed.kind]}. This
            cannot be undone.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction variant="destructive" onClick={() => doomed && onConfirm(doomed)}>
            Delete {doomed?.kind}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
