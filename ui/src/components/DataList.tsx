// The data of a project or task, as shown on right-click. Rows without a value are left out.
export function DataList({ rows }: { rows: [label: string, value: string | null | undefined][] }) {
  return (
    <dl className="grid gap-2 px-2 py-1.5">
      {rows
        .filter(([, value]) => value)
        .map(([label, value]) => (
          <div key={label}>
            <dt className="text-xs text-muted-foreground">{label}</dt>
            <dd className="break-words whitespace-pre-wrap">{value}</dd>
          </div>
        ))}
    </dl>
  )
}
