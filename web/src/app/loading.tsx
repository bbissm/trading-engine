/** Instant skeleton while the server loads the page data. */
export default function Loading() {
  return (
    <div className="animate-pulse space-y-4" aria-busy="true" aria-label="Lädt">
      <div className="h-7 w-40 rounded bg-surface-2" />
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="h-20 rounded-xl bg-surface-2" />
        ))}
      </div>
      <div className="h-48 rounded-xl bg-surface-2" />
      <div className="h-32 rounded-xl bg-surface-2" />
    </div>
  );
}
