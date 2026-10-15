import { describe, expect, test } from "vitest";
import { ageBucket, AGE_SEGMENTS, filledSegments } from "./age";

describe("ageBucket", () => {
  test.each([
    [0, "fresh"],
    [0.4, "fresh"],
    [1, "recent"],
    [2.9, "recent"],
    [3, "fading"],
    [6.9, "fading"],
    [7, "stale"],
    [40, "stale"],
  ])("%p days is %s", (days, expected) => {
    expect(ageBucket(days as number)).toBe(expected);
  });

  test("a negative age from clock skew is treated as fresh, not stale", () => {
    expect(ageBucket(-0.2)).toBe("fresh");
  });
});

describe("filledSegments", () => {
  test("a brand new posting fills every segment", () => {
    expect(filledSegments(0)).toBe(AGE_SEGMENTS);
  });

  test("a week-old posting is down to its last segment", () => {
    expect(filledSegments(7)).toBe(1);
  });

  test("the meter never empties completely, so a row is never ambiguous", () => {
    expect(filledSegments(365)).toBeGreaterThan(0);
  });

  test("the meter drains monotonically", () => {
    const runs = [0, 1, 3, 7, 30].map(filledSegments);

    expect(runs).toEqual([...runs].sort((a, b) => b - a));
  });
});
