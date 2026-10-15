import type { FeedGroup } from "./types";

export type DividerItem = { kind: "divider"; key: string; label: string; count: number };
export type FeedItem = DividerItem | { kind: "group"; group: FeedGroup };

/**
 * "TODAY" and "YESTERDAY" beat a date the reader has to subtract from today.
 *
 * The difference is rounded, not floored: local days are not all 24 hours long,
 * and after a daylight-saving transition a floored difference shifts every label
 * by one for the whole day. The two values being compared are always whole days
 * apart give or take an hour, so rounding is exact.
 */
export function dividerLabel(iso: string, now = new Date()): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "UNDATED";

  const days = Math.round(
    (startOfDay(now).getTime() - startOfDay(date).getTime()) / 86_400_000,
  );

  if (days <= 0) return "TODAY";
  if (days === 1) return "YESTERDAY";
  if (days < 7) return `${days} DAYS AGO`;

  // The year is included once it differs, so the same calendar day in two
  // hiring cycles cannot collapse into one divider.
  const sameYear = date.getFullYear() === now.getFullYear();
  return date
    .toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
      ...(sameYear ? {} : { year: "numeric" }),
    })
    .toUpperCase();
}

/**
 * Chunk the rail by day.
 *
 * Single pass over the groups, closing a divider as soon as the label changes.
 * An earlier version re-scanned the whole remainder for each divider, which
 * made a day absorb the count of any later day sharing its label.
 */
export function withDividers(groups: FeedGroup[], now = new Date()): FeedItem[] {
  const out: FeedItem[] = [];
  let divider: DividerItem | null = null;

  groups.forEach((group, i) => {
    const label = dividerLabel(group.first_seen_at, now);

    if (!divider || divider.label !== label) {
      divider = { kind: "divider", key: `d-${i}-${label}`, label, count: 0 };
      out.push(divider);
    }
    // Counts postings rather than groups: the number conveys the day's volume,
    // and one group can hold six postings.
    divider.count += group.posting_count;
    out.push({ kind: "group", group });
  });

  return out;
}

function startOfDay(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate());
}
