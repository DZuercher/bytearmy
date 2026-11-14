import type { Project } from '@/api/client'
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

// Asks before a project is deleted. `project` is the one to delete, or null when closed.
export function DeleteProjectDialog({
  project,
  onCancel,
  onConfirm,
}: {
  project: Project | null
  onCancel: () => void
  onConfirm: (project: Project) => void
}) {
  return (
    <AlertDialog open={project !== null} onOpenChange={(open) => !open && onCancel()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete project “{project?.name}”?</AlertDialogTitle>
          <AlertDialogDescription>
            All data associated with this project will be deleted: the directory{' '}
            <code className="break-all">{project?.path}</code> with every task in it. This cannot be
            undone.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction variant="destructive" onClick={() => project && onConfirm(project)}>
            Delete project
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
