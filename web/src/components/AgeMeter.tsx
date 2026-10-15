import { AGE_SEGMENTS, ageBucket, filledSegments } from "../lib/age";

const LABEL: Record<string, string> = {
  fresh: "posted today",
  recent: "a few days old",
  fading: "over three days old",
  stale: "over a week old",
};

/**
 * The signature detail: a segmented decay indicator instead of a date string.
 *
 * Colour alone never carries the meaning - the segment count encodes it too,
 * and the accessible name gives the literal number of days. That matters here
 * because urgency is the one thing this element exists to communicate.
 */
export function AgeMeter({ days }: { days: number }) {
  const filled = filledSegments(days);
  const bucket = ageBucket(days);
  const rounded = Math.max(0, Math.round(days));

  return (
    <span
      className="inline-flex items-center gap-[3px]"
      role="img"
      aria-label={`Open ${rounded} ${rounded === 1 ? "day" : "days"} - ${LABEL[bucket]}`}
      title={`Open ${rounded} ${rounded === 1 ? "day" : "days"}`}
      data-age={bucket}
    >
      {Array.from({ length: AGE_SEGMENTS }, (_, i) => (
        <span
          key={i}
          aria-hidden="true"
          className="h-[11px] w-[3px] rounded-full transition-[background-color,opacity] duration-300"
          style={{
            backgroundColor: i < filled ? "var(--age-full)" : "var(--age-empty)",
            // The lead segment stays bright; the rest desaturate as they fill,
            // so a full meter still reads as a gradient rather than a block.
            opacity: i < filled ? 1 - i * 0.18 : 1,
          }}
        />
      ))}
    </span>
  );
}
