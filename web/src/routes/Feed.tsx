import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, fetchFeed, fetchHealth } from "../lib/api";
import { mergeGroups } from "../lib/feed";
import type { FeedGroup, SystemHealth } from "../lib/types";
import { DateDivider, dividerLabel } from "../components/DateDivider";
import { PostingRow } from "../components/PostingRow";
import { FreshBand } from "../components/FreshBand";
import { CaughtUp, FeedError, FirstRun, HealthDot, Skeleton } from "../components/FeedStates";

export function Feed() {
  const [groups, setGroups] = useState<FeedGroup[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [freshCount, setFreshCount] = useState(0);
  const [lastSeenAt, setLastSeenAt] = useState<string | null>(null);
  const [health, setHealth] = useState<SystemHealth | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [canadaOnly, setCanadaOnly] = useState(false);

  // Guards against a second page landing after the filter changed and
  // stitching rows from two different queries into one list.
  const requestId = useRef(0);

  const load = useCallback(
    async (opts: { cursor?: string | null; append?: boolean; canadaOnly: boolean }) => {
      const id = ++requestId.current;
      opts.append ? setLoadingMore(true) : setLoading(true);
      setError(null);

      try {
        const page = await fetchFeed({ cursor: opts.cursor, canadaOnly: opts.canadaOnly });
        if (id !== requestId.current) return;

        setGroups((prev) => (opts.append ? mergeGroups(prev, page.groups) : page.groups));
        setCursor(page.next_cursor);
        setFreshCount(page.fresh_count);
        setLastSeenAt(page.last_seen_at);
      } catch (err) {
        if (id !== requestId.current) return;
        setError(err instanceof ApiError ? err.message : "Something went wrong.");
      } finally {
        if (id === requestId.current) {
          setLoading(false);
          setLoadingMore(false);
        }
      }
    },
    [],
  );

  useEffect(() => {
    load({ canadaOnly });
  }, [load, canadaOnly]);

  useEffect(() => {
    // Health is advisory: if it fails the feed is still perfectly usable, so a
    // failure here must not surface as a feed error.
    fetchHealth()
      .then(setHealth)
      .catch(() => setHealth(null));
  }, []);

  const totalPostings = groups.reduce((n, g) => n + g.posting_count, 0);

  return (
    <div className="mx-auto w-full max-w-[46rem] px-5 pb-28">
      <header className="flex flex-wrap items-center justify-between gap-3 pt-10 pb-6">
        <div>
          <h1 className="font-[family-name:var(--font-title)] text-[1.75rem] leading-none text-[var(--text)]">
            The Wire
          </h1>
          <p className="mt-1.5 text-[0.8rem] text-[var(--text-dim)]">
            New internship and new-grad roles, newest first.
          </p>
        </div>

        <div className="flex items-center gap-4">
          <HealthDot health={health} />
          <button
            type="button"
            onClick={() => setCanadaOnly((v) => !v)}
            aria-pressed={canadaOnly}
            className="rounded-[var(--radius-sm)] border px-2.5 py-1 text-[0.75rem] transition-colors"
            style={{
              borderColor: canadaOnly ? "var(--fresh)" : "var(--border-strong)",
              color: canadaOnly ? "var(--fresh)" : "var(--text-dim)",
            }}
          >
            Canada only
          </button>
        </div>
      </header>

      {loading ? (
        <Skeleton />
      ) : error ? (
        <FeedError message={error} onRetry={() => load({ canadaOnly })} />
      ) : groups.length === 0 ? (
        health?.state === "unknown" ? (
          <FirstRun />
        ) : (
          <CaughtUp />
        )
      ) : (
        <>
          <FreshBand freshCount={freshCount} total={totalPostings} lastSeenAt={lastSeenAt} />

          {/* The rail. One vertical line the whole column hangs from, because
              time is the primary axis of this product. */}
          <div
            className="relative mt-1"
            style={{ ["--rail-x" as string]: "1.75rem" }}
          >
            <span
              aria-hidden="true"
              className="absolute top-2 bottom-2 left-[12px] w-px"
              style={{ backgroundColor: "var(--rail)" }}
            />

            <ol className="list-none p-0">
              {withDividers(groups).map((item) =>
                item.kind === "divider" ? (
                  <li key={`d-${item.label}`}>
                    <DateDivider label={item.label} count={item.count} />
                  </li>
                ) : (
                  <li key={`${item.group.company_id}-${item.group.title}`}>
                    <PostingRow group={item.group} />
                  </li>
                ),
              )}
            </ol>
          </div>

          {cursor && (
            <div className="pt-8 pl-[var(--rail-x)]">
              <button
                type="button"
                disabled={loadingMore}
                onClick={() => load({ cursor, append: true, canadaOnly })}
                className="w-full rounded-[var(--radius)] border border-[var(--border)] py-2.5 text-[0.85rem] text-[var(--text-dim)] transition-colors hover:border-[var(--border-strong)] hover:text-[var(--text)] disabled:opacity-50"
              >
                {loadingMore ? "Loading..." : "Older postings"}
              </button>
            </div>
          )}

          {!cursor && groups.length > 0 && (
            <p className="pt-10 text-center text-[0.78rem] text-[var(--text-faint)]">
              That is everything open right now.
            </p>
          )}
        </>
      )}
    </div>
  );
}

type Item =
  | { kind: "divider"; label: string; count: number }
  | { kind: "group"; group: FeedGroup };

/** Chunk the rail by day, counting postings rather than groups: the count is
 * meant to convey the day's volume, and a group can hold six of them. */
function withDividers(groups: FeedGroup[]): Item[] {
  const out: Item[] = [];
  let current: string | null = null;

  for (let i = 0; i < groups.length; i++) {
    const label = dividerLabel(groups[i].first_seen_at);
    if (label !== current) {
      const count = groups
        .slice(i)
        .filter((g) => dividerLabel(g.first_seen_at) === label)
        .reduce((n, g) => n + g.posting_count, 0);
      out.push({ kind: "divider", label, count });
      current = label;
    }
    out.push({ kind: "group", group: groups[i] });
  }

  return out;
}
