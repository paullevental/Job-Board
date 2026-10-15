export type AgeBucket = "fresh" | "recent" | "fading" | "stale";

/** Four segments: readable at a glance while scrolling. More would turn a
 * signal into a chart. */
export const AGE_SEGMENTS = 4;

/**
 * Days-open is the most actionable number in this product, because the whole
 * thesis is first-wave advantage. So it renders as visible decay rather than as
 * a "4 days ago" string the reader has to translate into urgency.
 *
 * The thresholds follow how internship postings actually behave: the first day
 * is the one that matters, the first three are still competitive, and a week in
 * you are applying behind a few hundred people.
 */
export function ageBucket(days: number): AgeBucket {
  if (days < 1) return "fresh"; // a negative age is clock skew, still fresh
  if (days < 3) return "recent";
  if (days < 7) return "fading";
  return "stale";
}

const FILL: Record<AgeBucket, number> = { fresh: 4, recent: 3, fading: 2, stale: 1 };

/** Never returns 0: a completely empty meter reads as "no data" rather than
 * "this one is old", and the two mean opposite things to someone triaging. */
export function filledSegments(days: number): number {
  return FILL[ageBucket(days)];
}
