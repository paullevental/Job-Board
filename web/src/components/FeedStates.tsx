import type { SystemHealth } from "../lib/types";

type HealthState = SystemHealth | null | "failed";

/**
 * Empty is not one state, it is four, and they call for opposite responses:
 * nothing new, never polled, the poller died, the API is down. A blank area
 * would collapse them into "something is wrong, unclear what".
 */

export function CaughtUp() {
  return (
    <Panel>
      <div className="text-[2rem] leading-none" aria-hidden="true">
        &#10003;
      </div>
      <h2 className="mt-3 font-[family-name:var(--font-title)] text-[1.5rem] text-[var(--text)]">
        Caught up
      </h2>
      <p className="mt-1.5 max-w-sm text-[0.9rem] text-[var(--text-dim)]">
        Every open internship and new-grad role has been triaged. The poller
        keeps watching; new postings land here within minutes.
      </p>
    </Panel>
  );
}

export function FirstRun() {
  return (
    <Panel>
      <h2 className="font-[family-name:var(--font-title)] text-[1.5rem] text-[var(--text)]">
        Nothing watched yet
      </h2>
      <p className="mt-1.5 max-w-md text-[0.9rem] text-[var(--text-dim)]">
        The feed fills once a poll has run. Three commands, in order:
      </p>
      <ol className="mt-4 w-full max-w-md space-y-2 text-left">
        {[
          ["python -m job_poller load-companies", "read the board list"],
          ["python -m job_poller verify --prune", "drop tokens that 404"],
          ["python -m job_poller poll", "first pass"],
        ].map(([cmd, why]) => (
          <li
            key={cmd}
            className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--surface)] px-3 py-2"
          >
            <code className="tabular text-[0.78rem] text-[var(--text)]">{cmd}</code>
            <span className="ml-2 text-[0.78rem] text-[var(--text-faint)]">{why}</span>
          </li>
        ))}
      </ol>
    </Panel>
  );
}

export function FilteredEmpty({ onClear }: { onClear: () => void }) {
  return (
    <Panel>
      <h2 className="font-[family-name:var(--font-title)] text-[1.5rem] text-[var(--text)]">
        No Canada-eligible roles open
      </h2>
      <p className="mt-1.5 max-w-md text-[0.9rem] text-[var(--text-dim)]">
        The filter emptied this list, not your triage. Only 13 of 204 open matches
        are Canada-eligible right now, and 19 of the 39 configured boards are
        returning 404 - most of them the Canadian ones.
      </p>
      <button
        type="button"
        onClick={onClear}
        className="mt-4 rounded-[var(--radius-sm)] border border-[var(--border-strong)] bg-[var(--surface)] px-3.5 py-1.5 text-[0.85rem] text-[var(--text)] transition-colors hover:border-[var(--fresh)] hover:text-[var(--fresh)]"
      >
        Show every role
      </button>
    </Panel>
  );
}

/** An empty feed means nothing until we know the poller is alive. Claiming
 * "caught up" here would be exactly the ambiguity invariant 3 forbids. */
export function HealthUnknown({ failed }: { failed: boolean }) {
  return (
    <Panel>
      <h2 className="font-[family-name:var(--font-title)] text-[1.5rem] text-[var(--text)]">
        {failed ? "Cannot confirm the poller is alive" : "Checking the poller"}
      </h2>
      <p className="mt-1.5 max-w-md text-[0.9rem] text-[var(--text-dim)]">
        {failed
          ? "The feed is empty, but the health check failed - so this could be nothing new, or a poller that died days ago. Check that the API and the watcher are both running."
          : "One moment."}
      </p>
    </Panel>
  );
}

export function FeedError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <Panel>
      <h2 className="font-[family-name:var(--font-title)] text-[1.5rem] text-[var(--text)]">
        Cannot load the feed
      </h2>
      <p className="mt-1.5 max-w-md text-[0.9rem] text-[var(--text-dim)]">{message}</p>
      <button
        type="button"
        onClick={onRetry}
        className="mt-4 rounded-[var(--radius-sm)] border border-[var(--border-strong)] bg-[var(--surface)] px-3.5 py-1.5 text-[0.85rem] text-[var(--text)] transition-colors hover:border-[var(--fresh)] hover:text-[var(--fresh)]"
      >
        Try again
      </button>
    </Panel>
  );
}

export function Skeleton() {
  return (
    <div className="space-y-2 pt-6" aria-busy="true" aria-label="Loading the feed">
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className="flex gap-3 pl-[var(--rail-x)] opacity-[var(--o)]" style={{ "--o": 1 - i * 0.22 } as React.CSSProperties}>
          <div className="h-[30px] w-[30px] shrink-0 rounded-[7px] bg-[var(--surface-raised)]" />
          <div className="flex-1 space-y-2 py-1">
            <div className="h-[15px] w-[62%] rounded bg-[var(--surface-raised)]" />
            <div className="h-[11px] w-[38%] rounded bg-[var(--surface-raised)]" />
          </div>
        </div>
      ))}
    </div>
  );
}

/** Poller liveness, so an empty feed is never ambiguous between "nothing new"
 * and "the watcher died three days ago". */
export function HealthDot({ health }: { health: HealthState }) {
  if (health === null) return null;

  if (health === "failed") {
    return (
      <span className="flex items-center gap-2 text-[0.75rem] text-[var(--text-dim)]">
        <span
          aria-hidden="true"
          className="h-[7px] w-[7px] rounded-full"
          style={{ backgroundColor: "var(--stage)" }}
        />
        <span>Poller state unknown</span>
      </span>
    );
  }

  const copy: Record<SystemHealth["state"], string> = {
    green: "Poller healthy",
    amber: "Poller is behind schedule",
    red: "Poller looks dead",
    unknown: "Never polled",
  };
  const colour: Record<SystemHealth["state"], string> = {
    green: "var(--fresh)",
    amber: "var(--stage)",
    red: "var(--rejected)",
    unknown: "var(--text-faint)",
  };

  const ago =
    health.stale_seconds == null
      ? null
      : health.stale_seconds < 3600
        ? `${Math.max(1, Math.round(health.stale_seconds / 60))}m ago`
        : `${Math.round(health.stale_seconds / 3600)}h ago`;

  return (
    <span className="flex items-center gap-2 text-[0.75rem] text-[var(--text-dim)]">
      <span
        aria-hidden="true"
        className="h-[7px] w-[7px] rounded-full"
        style={{ backgroundColor: colour[health.state] }}
      />
      <span>{copy[health.state]}</span>
      {ago && <span className="tabular text-[var(--text-faint)]">{ago}</span>}
    </span>
  );
}

function Panel({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center px-6 py-20 text-center">{children}</div>
  );
}
