import type { Task } from '@/api/client'

export type State = Task['state']

// The states of a task, with their names and the colors they give it in the bars.
// backlog and new come from bytearmy, manual only from the user, the others from agents.
export const STATES: Record<State, { label: string; color: string }> = {
  backlog: { label: 'Backlog', color: '' },
  new: { label: 'New', color: '' },
  working: { label: 'Working', color: 'border-l-task-blue-mark bg-task-blue' },
  needs_input: { label: 'Needs input', color: 'border-l-task-amber-mark bg-task-amber' },
  waiting: { label: 'Waiting', color: 'border-l-task-red-mark bg-task-red' },
  done: { label: 'Done', color: 'border-l-task-green-mark bg-task-green' },
  merged: { label: 'Merged', color: 'border-l-task-purple-mark bg-task-purple' },
  manual: { label: 'Manual', color: 'border-l-task-black-mark bg-task-black' },
}
