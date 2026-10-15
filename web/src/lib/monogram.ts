/**
 * Company identity without logos.
 *
 * No logo assets exist for these 39 companies and none will be fabricated, so
 * recognition comes from a deterministic monogram: two letters plus a hue
 * hashed from the name. A given company is therefore always the same colour,
 * with nothing stored and nothing fetched.
 */

export function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return "??";
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[1][0]).toUpperCase();
}

export function hueFor(name: string): number {
  // FNV-1a: tiny, stable across runs and machines, and it spreads short strings
  // far better than summing char codes would - that would hand "Ramp" and
  // "Pram" the same colour.
  let hash = 0x811c9dc5;
  for (let i = 0; i < name.length; i++) {
    hash ^= name.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  return Math.abs(hash) % 360;
}
