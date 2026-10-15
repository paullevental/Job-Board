/**
 * How much is new since the last visit.
 *
 * In phase 5 this drains as rows are triaged and a "Caught up" control advances
 * the marker. Until the write endpoints exist it reports honestly and offers no
 * control it cannot honour - a dead button would be worse than none.
 */
export function FreshBand({
  freshCount,
  total,
  lastSeenAt,
}: {
  freshCount: number;
  total: number;
  lastSeenAt: string | null;
}) {
  if (total === 0) return null;

  // `freshCount` counts the whole feed while `total` counts what is loaded, so
  // the raw pair can read "204 of 25". Clamped, because an ARIA value above its
  // own maximum is announced verbatim and means nothing.
  const shown = Math.min(freshCount, total);
  const pct = Math.min(100, Math.round((shown / Math.max(total, 1)) * 100));

  return (
    <section
      aria-label="New since your last visit"
      className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface)] px-4 py-3"
    >
      <div className="flex items-baseline justify-between gap-4">
        <h2 className="text-[0.85rem] text-[var(--text-dim)]">
          <span className="tabular text-[1.35rem] font-medium text-[var(--fresh)]">
            {freshCount}
          </span>{" "}
          {lastSeenAt ? "new since your last visit" : "open and untriaged"}
        </h2>
        <span className="tabular text-[0.72rem] text-[var(--text-faint)]">
          {total} shown
        </span>
      </div>

      {/* The drain. Static in this phase; phase 5 animates it down as rows are
          triaged, which is what makes finishing a state you can see arriving. */}
      <div
        className="mt-2.5 h-[3px] w-full overflow-hidden rounded-full"
        style={{ backgroundColor: "var(--age-empty)" }}
        role="progressbar"
        aria-valuenow={shown}
        aria-valuemin={0}
        aria-valuemax={total}
      >
        <div
          className="h-full rounded-full transition-[width] duration-500"
          style={{ width: `${pct}%`, backgroundColor: "var(--fresh)" }}
        />
      </div>
    </section>
  );
}
