import {
  DndContext,
  type DragEndEvent,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
} from '@dnd-kit/core'
import {
  SortableContext,
  arrayMove,
  horizontalListSortingStrategy,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import type { HTMLAttributes, ReactNode } from 'react'

// A list whose items can be dragged into a new order. `ids` is the current order.
export function SortableList({
  ids,
  horizontal = false,
  onReorder,
  className,
  children,
}: {
  ids: string[]
  horizontal?: boolean
  onReorder: (order: string[]) => void
  className?: string
  children: ReactNode
}) {
  // A drag starts only after a few pixels of movement, so a plain click still clicks.
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  )

  function onDragEnd({ active, over }: DragEndEvent) {
    if (!over || active.id === over.id) return
    onReorder(arrayMove(ids, ids.indexOf(String(active.id)), ids.indexOf(String(over.id))))
  }

  return (
    <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
      <SortableContext
        items={ids}
        strategy={horizontal ? horizontalListSortingStrategy : verticalListSortingStrategy}
      >
        <ul className={className}>{children}</ul>
      </SortableContext>
    </DndContext>
  )
}

// One item of a SortableList. `children` gets the props that make an element the drag handle.
export function SortableItem({
  id,
  className,
  children,
}: {
  id: string
  className?: string
  children: (handle: HTMLAttributes<HTMLElement>, isDragging: boolean) => ReactNode
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id })

  return (
    <li
      ref={setNodeRef}
      className={className}
      style={{ transform: CSS.Translate.toString(transform), transition }}
    >
      {children({ ...attributes, ...listeners } as HTMLAttributes<HTMLElement>, isDragging)}
    </li>
  )
}
