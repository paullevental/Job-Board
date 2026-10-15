/** Date dividers carry counts so the day's volume reads before any individual
 * posting does, and they chunk an otherwise uniform wall of rows into units the
 * reader can finish. */
export function DateDivider({ label, count }: { label: string; count: number }) {
  return (
    <div className="relative flex items-center gap-3 pt-7 pb-2 pl-[var(--rail-x)]">
      <span
        aria-hidden="true"
        className="absolute left-[8px] h-[9px] w-[9px] rotate-45 border border-[var(--border-strong)] bg-[var(--ground)]"
      />
      <h2 className="tabular text-[0.68rem] font-medium tracking-[0.16em] text-[var(--text-dim)] uppercase">
        {label}
      </h2>
      <span className="tabular text-[0.68rem] text-[var(--text-faint)]">
        &middot; {count}
      </span>
      <span aria-hidden="true" className="h-px flex-1 bg-[var(--border)]" />
    </div>
  );
}

/** "TODAY" and "YESTERDAY" beat a date the reader has to subtract from today. */
export function dividerLabel(iso: string, now = new Date()): string {
  const date = new Date(iso);
  const days = Math.floor(
    (startOfDay(now).getTime() - startOfDay(date).getTime()) / 86_400_000,
  );

  if (days <= 0) return "TODAY";
  if (days === 1) return "YESTERDAY";
  if (days < 7) return `${days} DAYS AGO`;

  return date
    .toLocaleDateString(undefined, { month: "short", day: "numeric" })
    .toUpperCase();
}

function startOfDay(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate());
}
