import { hueFor, initials } from "../lib/monogram";

/** Two letters on a hue hashed from the company name. Honest recognition
 * without inventing a logo that does not exist. */
export function MonogramChip({ name, size = 30 }: { name: string; size?: number }) {
  const hue = hueFor(name);

  return (
    <span
      aria-hidden="true"
      className="inline-grid shrink-0 place-items-center rounded-[7px] font-mono font-medium select-none"
      style={{
        width: size,
        height: size,
        fontSize: size * 0.37,
        letterSpacing: "0.02em",
        // Low-saturation fill with a brighter text and a hairline border of the
        // same hue: enough to distinguish 39 companies without any chip
        // shouting louder than the posting title next to it.
        backgroundColor: `oklch(0.3 0.055 ${hue})`,
        color: `oklch(0.86 0.09 ${hue})`,
        border: `1px solid oklch(0.42 0.07 ${hue})`,
      }}
    >
      {initials(name)}
    </span>
  );
}
