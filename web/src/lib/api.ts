import type { FeedPage, SystemHealth } from "./types";

/** The API's error envelope, `{error: {code, message}}`, as a throwable.
 *
 * The server promises every failure arrives in that shape, so parsing it in one
 * place means no call site has to guess what a failed response looks like. */
export class ApiError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, init);
  } catch {
    // The API being down is a normal state here, not an exception: the poller
    // and the server are separate processes and either can be stopped.
    throw new ApiError("offline", "Cannot reach the API. Is the server running?", 0);
  }

  if (!response.ok) {
    const fallback = `Request failed (${response.status})`;
    let code = "http_error";
    let message = fallback;
    try {
      const body = await response.json();
      code = body?.error?.code ?? code;
      message = body?.error?.message ?? fallback;
    } catch {
      // A non-JSON body means something upstream of the app failed; the status
      // is all we honestly know.
    }
    throw new ApiError(code, message, response.status);
  }

  return (await response.json()) as T;
}

export interface FeedQuery {
  cursor?: string | null;
  limit?: number;
  canadaOnly?: boolean;
  engineeringOnly?: boolean;
}

export function fetchFeed({ cursor, limit, canadaOnly, engineeringOnly }: FeedQuery = {}) {
  const params = new URLSearchParams();
  if (cursor) params.set("cursor", cursor);
  if (limit) params.set("limit", String(limit));
  if (canadaOnly) params.set("canada_only", "true");
  if (engineeringOnly) params.set("engineering_only", "true");

  const query = params.toString();
  return request<FeedPage>(`/api/feed${query ? `?${query}` : ""}`);
}

export function fetchHealth() {
  return request<SystemHealth>("/api/system/health");
}
