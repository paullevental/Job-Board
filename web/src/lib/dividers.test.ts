import { describe, expect, test } from "vitest";
import { dividerLabel, withDividers } from "./dividers";
import type { FeedGroup } from "./types";

const group = (iso: string, postingCount = 1): FeedGroup =>
  ({
    company_id: 1,
    company_name: "Acme",
    title: `Role ${iso}`,
    posting_count: postingCount,
    locations: ["Toronto, ON"],
    first_seen_at: iso,
    postings: Array.from({ length: postingCount }, (_, i) => ({ id: i })),
  }) as FeedGroup;

describe("dividerLabel", () => {
  test("labels the current day TODAY", () => {
    const now = new Date(2026, 9, 15, 12, 0);

    expect(dividerLabel(new Date(2026, 9, 15, 9, 0).toISOString(), now)).toBe("TODAY");
  });

  test("survives a daylight-saving transition", () => {
    // Spring-forward makes the previous local day 23 hours long, so flooring a
    // millisecond difference by 24h shifts every label by one for a whole day.
    const now = new Date(2026, 2, 9, 12, 0); // day after the 2026-03-08 change
    const yesterday = new Date(2026, 2, 8, 12, 0);
    const twoDaysAgo = new Date(2026, 2, 7, 12, 0);

    expect(dividerLabel(yesterday.toISOString(), now)).toBe("YESTERDAY");
    expect(dividerLabel(twoDaysAgo.toISOString(), now)).toBe("2 DAYS AGO");
  });

  test("distinguishes the same calendar day in different years", () => {
    const now = new Date(2027, 0, 20, 12, 0);

    const thisCycle = dividerLabel(new Date(2026, 9, 7).toISOString(), now);
    const lastCycle = dividerLabel(new Date(2025, 9, 7).toISOString(), now);

    expect(thisCycle).not.toBe(lastCycle);
  });

  test("an unparseable date does not become a heading reading INVALID DATE", () => {
    expect(dividerLabel("not-a-date")).toBe("UNDATED");
  });
});

describe("withDividers", () => {
  test("counts postings, not groups", () => {
    const now = new Date(2026, 9, 15, 12, 0);

    const items = withDividers([group(new Date(2026, 9, 15, 9, 0).toISOString(), 3)], now);

    expect(items[0]).toMatchObject({ kind: "divider", count: 3 });
  });

  test("a day's divider counts only its own run, not every later match", () => {
    // Two dates that share a label unless the year is included, with another day
    // between them - the exact shape that made one divider absorb the other.
    const now = new Date(2027, 0, 20, 12, 0);
    const items = withDividers(
      [
        group(new Date(2026, 9, 7).toISOString(), 2),
        group(new Date(2026, 8, 3).toISOString(), 1),
        group(new Date(2025, 9, 7).toISOString(), 4),
      ],
      now,
    );

    const counts = items.filter((i) => i.kind === "divider").map((i) => i.count);
    expect(counts).toEqual([2, 1, 4]);
  });

  test("every divider is uniquely identifiable", () => {
    const now = new Date(2027, 0, 20, 12, 0);
    const items = withDividers(
      [
        group(new Date(2026, 9, 7).toISOString()),
        group(new Date(2026, 8, 3).toISOString()),
        group(new Date(2025, 9, 7).toISOString()),
      ],
      now,
    );

    const keys = items.filter((i) => i.kind === "divider").map((i) => i.key);
    expect(new Set(keys).size).toBe(keys.length);
  });

  test("consecutive groups from one day share a single divider", () => {
    const now = new Date(2026, 9, 15, 12, 0);
    const items = withDividers(
      [
        group(new Date(2026, 9, 15, 9, 0).toISOString(), 2),
        group(new Date(2026, 9, 15, 8, 0).toISOString(), 1),
      ],
      now,
    );

    const dividers = items.filter((i) => i.kind === "divider");
    expect(dividers).toHaveLength(1);
    expect(dividers[0].count).toBe(3);
  });
});
