import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, fetchFeed, fetchHealth } from "../lib/api";
import { mergeGroups } from "../lib/feed";
import { withDividers } from "../lib/dividers";
import type { FeedGroup, SystemHealth } from "../lib/types";
import { DateDivider } from "../components/DateDivider";
import { PostingRow } from "../components/PostingRow";
import { FreshBand } from "../components/FreshBand";
import {
  CaughtUp,
  FeedError,
  FilteredEmpty,
  FirstRun,
  HealthDot,
  HealthUnknown,
  Skeleton,
} from "../components/FeedStates";

/** `null` while the health call is in flight, `"failed"` once it has failed.
 * The two must stay distinct: an empty feed beside an unknown poller state is
 * not "caught up", it is "we cannot tell", and claiming the former would break
 * the project's third invariant. */
type HealthState = SystemHealth | null | "failed";

export function Feed() {
  const [groups, setGroups] = useState<FeedGroup[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [freshCount, setFreshCount] = useState(0);
  const [lastSeenAt, setLastSeenAt] = useState<string | null>(null);
  const [health, setHealth] = useState<HealthState>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pageError, setPageError] = useState<string | null>(null);
  const [canadaOnly, setCanadaOnly] = useState(false);

  // Discards a response whose request has been superseded - a filter toggled
  // while a page was in flight, or StrictMode's double effect in development.
  const requestId = useRef(0);
  // Closes the window between a click and React disabling the button, where a
  // second click could fire a duplicate request and strand `loadingMore`.
  const inFlight = useRef(false);
  const endOfFeed = useRef<HTMLParagraphElement>(null);
  const wasPaging = useRef(false);

  const load = useCallback(
    async (opts: { cursor?: string | null; append?: boolean; canadaOnly: boolean }) => {
      if (opts.append && inFlight.current) return;

      const id = ++requestId.current;
      inFlight.current = true;
      if (opts.append) {
        setLoadingMore(true);
        setPageError(null);
      } else {
        setLoading(true);
        setError(null);
        setPageError(null);
      }

      try {
        const page = await fetchFeed({ cursor: opts.cursor, canadaOnly: opts.canadaOnly });
        if (id !== requestId.current) return;

        setGroups((prev) => (opts.append ? mergeGroups(prev, page.groups) : page.groups));
        setCursor(page.next_cursor);
        setFreshCount(page.fresh_count);
        setLastSeenAt(page.last_seen_at);
      } catch (err) {
        if (id !== requestId.current) return;
        const message = err instanceof ApiError ? err.message : "Something went wrong.";
        // A failed page is not a failed feed: the rows already read stay put.
        opts.append ? setPageError(message) : setError(message);
      } finally {
        if (id === requestId.current) {
          inFlight.current = false;
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
    fetchHealth()
      .then(setHealth)
      .catch(() => setHealth("failed"));
  }, []);

  // Reaching the end unmounts the button the keyboard user was on, dropping
  // focus to <body> after they paged through hundreds of rows.
  useEffect(() => {
    if (wasPaging.current && cursor === null) endOfFeed.current?.focus();
    wasPaging.current = cursor !== null;
  }, [cursor]);

  const totalPostings = groups.reduce((n, g) => n + g.posting_count, 0);
  const items = withDividers(groups);
  const announcement = loading
    ? "Loading the feed"
    : error
      ? `Could not load the feed: ${error}`
      : `${totalPostings} ${totalPostings === 1 ? "role" : "roles"} shown${
          canadaOnly ? ", Canada only" : ""
        }`;

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

      {/* One polite region describing the list, so a filter toggle or a new
          page is announced rather than silently swapping the content. */}
      <p aria-live="polite" className="sr-only">
        {announcement}
      </p>

      {loading ? (
        <Skeleton />
      ) : error ? (
        <FeedError message={error} onRetry={() => load({ canadaOnly })} />
      ) : groups.length === 0 ? (
        canadaOnly ? (
          <FilteredEmpty onClear={() => setCanadaOnly(false)} />
        ) : health === null || health === "failed" ? (
          <HealthUnknown failed={health === "failed"} />
        ) : health.state === "unknown" ? (
          <FirstRun />
        ) : (
          <CaughtUp />
        )
      ) : (
        <>
          <FreshBand freshCount={freshCount} total={totalPostings} lastSeenAt={lastSeenAt} />

          <div className="relative mt-1">
            <span
              aria-hidden="true"
              className="absolute top-2 bottom-2 left-[12px] w-px"
              style={{ backgroundColor: "var(--rail)" }}
            />

            {/* role="list" is explicit: Safari drops list semantics when
                list-style is none, losing "list, 25 items" in VoiceOver. */}
            <ol role="list" className="list-none p-0">
              {items.map((item) =>
                item.kind === "divider" ? (
                  <li key={item.key}>
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

          {pageError && (
            <p
              role="alert"
              className="mt-6 ml-[var(--rail-x)] rounded-[var(--radius-sm)] border border-[var(--rejected)] px-3 py-2 text-[0.8rem] text-[var(--rejected)]"
            >
              {pageError}
            </p>
          )}

          {cursor && (
            <div className="pt-8 pl-[var(--rail-x)]">
              <button
                type="button"
                disabled={loadingMore}
                onClick={() => load({ cursor, append: true, canadaOnly })}
                className="w-full rounded-[var(--radius)] border border-[var(--border)] py-2.5 text-[0.85rem] text-[var(--text-dim)] transition-colors hover:border-[var(--border-strong)] hover:text-[var(--text)] disabled:opacity-50"
              >
                {loadingMore ? "Loading..." : pageError ? "Try that page again" : "Older postings"}
              </button>
            </div>
          )}

          {!cursor && (
            <p
              ref={endOfFeed}
              tabIndex={-1}
              className="pt-10 text-center text-[0.78rem] text-[var(--text-faint)]"
            >
              That is everything open right now.
            </p>
          )}
        </>
      )}
    </div>
  );
}
