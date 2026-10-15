import { describe, expect, test } from "vitest";
import { mergeGroups } from "./feed";
import type { FeedGroup } from "./types";

const group = (companyId: number, title: string, ids: number[]): FeedGroup =>
  ({
    company_id: companyId,
    company_name: "Acme",
    title,
    posting_count: ids.length,
    locations: ids.map((id) => `City ${id}`),
    first_seen_at: "2026-10-01T12:00:00Z",
    postings: ids.map((id) => ({ id })),
  }) as FeedGroup;

describe("mergeGroups", () => {
  test("a group split across two pages becomes one group", () => {
    const merged = mergeGroups([group(1, "SWE Intern", [1, 2])], [group(1, "SWE Intern", [3])]);

    expect(merged).toHaveLength(1);
    expect(merged[0].postings.map((p) => p.id)).toEqual([1, 2, 3]);
    expect(merged[0].posting_count).toBe(3);
  });

  test("the same title at different companies stays separate", () => {
    const merged = mergeGroups([group(1, "SWE Intern", [1])], [group(2, "SWE Intern", [2])]);

    expect(merged).toHaveLength(2);
  });

  test("page order is preserved", () => {
    const merged = mergeGroups([group(1, "A", [1])], [group(2, "B", [2])]);

    expect(merged.map((g) => g.title)).toEqual(["A", "B"]);
  });

  test("a posting never appears twice if a page is re-delivered", () => {
    const page = [group(1, "SWE Intern", [1, 2])];

    const merged = mergeGroups(page, page);

    expect(merged[0].postings.map((p) => p.id)).toEqual([1, 2]);
    expect(merged[0].posting_count).toBe(2);
  });

  test("merging collects locations from both pages without duplicates", () => {
    const merged = mergeGroups([group(1, "SWE", [1])], [group(1, "SWE", [1, 2])]);

    expect(merged[0].locations).toEqual(["City 1", "City 2"]);
  });
});
