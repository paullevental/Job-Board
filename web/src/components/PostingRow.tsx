import type { FeedGroup } from "../lib/types";
import { AgeMeter } from "./AgeMeter";
import { MonogramChip } from "./MonogramChip";

/** One role on the rail. A group of postings that share a title renders as one
 * row carrying a location count, because 28% of feed rows are the same title
 * repeated across cities and ungrouped they stop the reader's eye working. */
export function PostingRow({ group }: { group: FeedGroup }) {
  const lead = group.postings[0];
  const term = [lead.season, lead.year].filter(Boolean).join(" ");
  const where =
    group.locations.length > 1
      ? `${group.locations.length} locations`
      : (group.locations[0] ?? "Location not given");

  return (
    <article className="relative pl-[var(--rail-x)]">
      {/* The node on the time rail. Fresh roles get a filled node; the rest a
          hollow one, so the eye can find today's arrivals while scrolling. */}
      <span
        aria-hidden="true"
        className="absolute left-[9px] top-[22px] h-[7px] w-[7px] rounded-full"
        style={{
          backgroundColor: lead.days_open < 1 ? "var(--fresh)" : "var(--ground)",
          border: `1px solid ${lead.days_open < 1 ? "var(--fresh)" : "var(--border-strong)"}`,
        }}
      />

      <div className="group rounded-[var(--radius)] border border-transparent px-3 py-3 transition-colors duration-150 hover:border-[var(--border)] hover:bg-[var(--surface)]">
        <div className="flex items-start gap-3">
          <MonogramChip name={group.company_name} />

          <div className="min-w-0 flex-1">
            <a
              href={lead.url}
              target="_blank"
              rel="noopener noreferrer"
              className="block font-[family-name:var(--font-title)] text-[1.3rem] leading-[1.25] text-[var(--text)] no-underline hover:underline decoration-[var(--fresh)] underline-offset-[3px]"
            >
              {group.title}
            </a>

            <div className="mt-1.5 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[0.8rem] text-[var(--text-dim)]">
              <span className="font-medium text-[var(--text)]">{group.company_name}</span>
              <Dot />
              <span>{where}</span>
              {term && (
                <>
                  <Dot />
                  <span className="tabular">{term}</span>
                </>
              )}
              {lead.canada_eligible && (
                <>
                  <Dot />
                  <span className="text-[var(--fresh)]">Canada</span>
                </>
              )}
              {lead.is_remote && (
                <>
                  <Dot />
                  <span>Remote</span>
                </>
              )}
            </div>
          </div>

          <div className="flex shrink-0 flex-col items-end gap-1.5 pt-0.5">
            <AgeMeter days={lead.days_open} />
            <span className="tabular text-[0.7rem] text-[var(--text-faint)]">
              {lead.category === "new_grad" ? "NEW GRAD" : "INTERN"}
            </span>
          </div>
        </div>
      </div>
    </article>
  );
}

function Dot() {
  return (
    <span aria-hidden="true" className="text-[var(--text-faint)]">
      &middot;
    </span>
  );
}
