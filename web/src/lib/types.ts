/** Mirrors the Pydantic schemas in `job_poller/api/schemas.py`. */

export interface Posting {
  id: number;
  company_id: number;
  company_name: string;
  ats: string;
  external_id: string;
  title: string;
  url: string;
  location: string;
  department: string;
  category: string;
  canada_eligible: boolean;
  is_remote: boolean;
  engineering: boolean;
  season: string | null;
  year: number | null;
  first_seen_at: string;
  last_seen_at: string;
  closed_at: string | null;
  is_open: boolean;
  days_open: number;
  status: string | null;
  application_id: number | null;
}

export interface FeedGroup {
  company_id: number;
  company_name: string;
  title: string;
  posting_count: number;
  locations: string[];
  first_seen_at: string;
  postings: Posting[];
}

export interface FeedPage {
  groups: FeedGroup[];
  next_cursor: string | null;
  fresh_count: number;
  last_seen_at: string | null;
}

export interface PollRun {
  id: number;
  started_at: string;
  finished_at: string | null;
  duration_s: number | null;
  boards_attempted: number;
  boards_ok: number;
  boards_failed: number;
  matches_new: number;
}

export interface SystemHealth {
  state: "green" | "amber" | "red" | "unknown";
  last_run: PollRun | null;
  stale_seconds: number | null;
  failing_boards: number;
}
