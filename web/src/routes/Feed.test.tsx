import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, test, vi } from "vitest";
import { Feed } from "./Feed";
import type { FeedPage, Posting, SystemHealth } from "../lib/types";

const posting = (over: Partial<Posting> = {}): Posting =>
  ({
    id: 1,
    company_id: 1,
    company_name: "Acme",
    ats: "greenhouse",
    external_id: "1",
    title: "SWE Intern",
    url: "https://boards.example.com/acme/1",
    location: "Toronto, ON",
    department: "Engineering",
    category: "intern",
    canada_eligible: true,
    is_remote: false,
    engineering: true,
    season: "Summer",
    year: 2027,
    first_seen_at: new Date().toISOString(),
    last_seen_at: new Date().toISOString(),
    closed_at: null,
    is_open: true,
    days_open: 0.2,
    status: null,
    application_id: null,
    ...over,
  }) as Posting;

const page = (over: Partial<FeedPage> = {}): FeedPage => ({
  groups: [
    {
      company_id: 1,
      company_name: "Acme",
      title: "SWE Intern",
      posting_count: 2,
      locations: ["Toronto, ON", "Vancouver, BC"],
      first_seen_at: new Date().toISOString(),
      postings: [posting(), posting({ id: 2, location: "Vancouver, BC" })],
    },
  ],
  next_cursor: null,
  fresh_count: 2,
  last_seen_at: null,
  ...over,
});

const health = (state: SystemHealth["state"]): SystemHealth => ({
  state,
  last_run: null,
  stale_seconds: state === "unknown" ? null : 60,
  failing_boards: 0,
});

function mockApi(feed: FeedPage | Error, state: SystemHealth["state"] = "green") {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.startsWith("/api/system/health")) {
        return new Response(JSON.stringify(health(state)), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }
      if (feed instanceof Error) {
        return new Response(
          JSON.stringify({ error: { code: "internal_error", message: "the boards are down" } }),
          { status: 500, headers: { "content-type": "application/json" } },
        );
      }
      return new Response(JSON.stringify(feed), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }),
  );
}

beforeEach(() => vi.unstubAllGlobals());

describe("the wire", () => {
  test("renders a role once, with its location count", async () => {
    mockApi(page());

    render(<Feed />);

    expect(await screen.findByText("SWE Intern")).toBeInTheDocument();
    expect(screen.getByText("2 locations")).toBeInTheDocument();
  });

  test("a single-location role names the place instead of counting", async () => {
    const one = page();
    one.groups[0].locations = ["Toronto, ON"];
    one.groups[0].posting_count = 1;
    mockApi(one);

    render(<Feed />);

    expect(await screen.findByText("Toronto, ON")).toBeInTheDocument();
  });

  test("groups sit under a date divider carrying the day's posting count", async () => {
    mockApi(page());

    render(<Feed />);

    // Counts postings, not groups: one group of two locations is two postings,
    // because the number is meant to convey the day's volume.
    const divider = (await screen.findByRole("heading", { name: "TODAY" })).parentElement!;

    expect(within(divider).getByText(/2/)).toBeInTheDocument();
  });

  test("the age meter states the real age for screen readers", async () => {
    mockApi(page());

    render(<Feed />);

    expect(await screen.findByLabelText(/Open 0 days/i)).toBeInTheDocument();
  });

  test("an empty feed shows the caught-up state, not a blank area", async () => {
    mockApi(page({ groups: [], fresh_count: 0 }));

    render(<Feed />);

    expect(await screen.findByText("Caught up")).toBeInTheDocument();
  });

  test("an empty feed on a never-polled database offers the onboarding path", async () => {
    mockApi(page({ groups: [], fresh_count: 0 }), "unknown");

    render(<Feed />);

    expect(await screen.findByText("Nothing watched yet")).toBeInTheDocument();
    expect(screen.getByText(/load-companies/)).toBeInTheDocument();
  });

  test("an API failure explains itself and offers a retry", async () => {
    mockApi(new Error("boom"));

    render(<Feed />);

    expect(await screen.findByText(/cannot load the feed/i)).toBeInTheDocument();
    expect(screen.getByText("the boards are down")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
  });

  test("a dead poller is visible next to the feed", async () => {
    mockApi(page(), "red");

    render(<Feed />);

    await waitFor(() => expect(screen.getByText(/poller looks dead/i)).toBeInTheDocument());
  });

  test("the fresh band reports what is new without offering a control it cannot honour", async () => {
    mockApi(page({ fresh_count: 2 }));

    render(<Feed />);

    expect(await screen.findByLabelText("New since your last visit")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /caught up/i })).not.toBeInTheDocument();
  });

  test("the last page says so rather than showing a dead load-more button", async () => {
    mockApi(page({ next_cursor: null }));

    render(<Feed />);

    expect(await screen.findByText(/everything open right now/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /older postings/i })).not.toBeInTheDocument();
  });
});

describe("states that must never lie", () => {
  test("a failed health check does not become a false Caught up", async () => {
    // Invariant 3: an empty feed must never be ambiguous between "nothing new"
    // and "the poller died". With health unknown we cannot claim either.
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.startsWith("/api/system/health")) return new Response("", { status: 500 });
        return new Response(JSON.stringify(page({ groups: [], fresh_count: 0 })), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }),
    );

    render(<Feed />);

    expect(await screen.findByText(/cannot confirm/i)).toBeInTheDocument();
    expect(screen.queryByText("Caught up")).not.toBeInTheDocument();
  });

  test("an empty filtered feed says the filter emptied it", async () => {
    const user = userEvent.setup();
    let filtered = false;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.startsWith("/api/system/health"))
          return new Response(JSON.stringify(health("green")), {
            status: 200,
            headers: { "content-type": "application/json" },
          });
        const body = url.includes("canada_only")
          ? page({ groups: [], fresh_count: 0 })
          : page();
        if (url.includes("canada_only")) filtered = true;
        return new Response(JSON.stringify(body), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }),
    );

    render(<Feed />);
    await screen.findByText("SWE Intern");
    await user.click(screen.getByRole("button", { name: /canada only/i }));

    expect(filtered).toBe(true);
    expect(await screen.findByText(/no canada-eligible roles/i)).toBeInTheDocument();
    expect(screen.queryByText("Caught up")).not.toBeInTheDocument();
  });

  test("a failed older-postings page keeps the rows already loaded", async () => {
    const user = userEvent.setup();
    let calls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.startsWith("/api/system/health"))
          return new Response(JSON.stringify(health("green")), {
            status: 200,
            headers: { "content-type": "application/json" },
          });
        calls += 1;
        if (calls > 1) {
          return new Response(
            JSON.stringify({ error: { code: "internal_error", message: "page failed" } }),
            { status: 500, headers: { "content-type": "application/json" } },
          );
        }
        return new Response(JSON.stringify(page({ next_cursor: "c1" })), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }),
    );

    render(<Feed />);
    await screen.findByText("SWE Intern");
    await user.click(screen.getByRole("button", { name: /older postings/i }));

    // The failure concerns one page, not the column the user already read.
    expect(await screen.findByText(/page failed/i)).toBeInTheDocument();
    expect(screen.getByText("SWE Intern")).toBeInTheDocument();
  });

  test("the feed announces its state to assistive technology", async () => {
    mockApi(page());

    render(<Feed />);
    await screen.findByText("SWE Intern");

    const live = document.querySelector("[aria-live]");
    expect(live).not.toBeNull();
    expect(live!.textContent).toMatch(/role/i);
  });

  test("the fresh band never reports more than its maximum", async () => {
    mockApi(page({ fresh_count: 204 }));

    render(<Feed />);
    const bar = await screen.findByRole("progressbar");

    expect(Number(bar.getAttribute("aria-valuenow"))).toBeLessThanOrEqual(
      Number(bar.getAttribute("aria-valuemax")),
    );
  });
});
