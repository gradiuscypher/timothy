import { screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { PoolDetail } from "@/routes/PoolDetail";

import { SIGNED_IN, apiUrl, get, mockApi, renderWithQuery, server } from "./harness";

mockApi();

/**
 * Putting names to the IDs on a page.
 *
 * The name is a cache the backend fills from traffic it already has, so "no name" is an
 * ordinary answer rather than a failure — and the ID has to survive either way, because
 * it is what a moderator pastes into Discord and what two people with the same display
 * name do not share.
 */

const POOL = {
  id: 1,
  name: "spam",
  description: null,
  created_by: "user:200000000000000001",
  created_at: "2026-08-01T00:00:00Z",
};

const LISTING = {
  id: 1,
  pool_id: 1,
  pool_name: "spam",
  user_id: "300000000000000001",
  reason: "ban evasion",
  created_by: "user:200000000000000001",
  created_at: "2026-08-01T00:00:00Z",
};

/** The listing table, with whatever the resolver is set up to know. */
function openPool(names: Array<{ user_id: string; name: string }>) {
  const asked: URLSearchParams[] = [];
  server.use(
    get("/auth/me", SIGNED_IN),
    get("/pools/spam", POOL),
    get("/pools/spam/listings", { listings: [LISTING], next_after_id: null, total: 1 }),
    http.get(apiUrl("/users/names"), ({ request }) => {
      asked.push(new URL(request.url).searchParams);
      return HttpResponse.json(
        names.map((known) => ({ ...known, observed_at: "2026-08-01T00:00:00Z" })),
      );
    }),
  );
  renderWithQuery(<PoolDetail name="spam" />);
  return asked;
}

describe("naming the IDs on a page", () => {
  it("shows the last known name above the ID", async () => {
    openPool([{ user_id: "300000000000000001", name: "Nuisance" }]);

    expect(await screen.findByText("Nuisance")).toBeInTheDocument();
    // The ID stays. It is what gets pasted into Discord's search, and what the row is
    // actually keyed by — a display name is neither unique nor permanent.
    expect(screen.getByText("300000000000000001")).toBeInTheDocument();
  });

  it("shows the ID alone for somebody Timothy has never seen a name for", async () => {
    openPool([]);

    expect(await screen.findByText("300000000000000001")).toBeInTheDocument();
  });

  it("names whoever listed them as well as the listed user", async () => {
    openPool([
      { user_id: "300000000000000001", name: "Nuisance" },
      { user_id: "200000000000000001", name: "gradius" },
    ]);

    expect(await screen.findByText("Nuisance")).toBeInTheDocument();
    expect(screen.getByText("gradius")).toBeInTheDocument();
  });

  it("asks about every ID on the page in one request", async () => {
    // One resolution for the table, not one per row: the alternative is fifty requests
    // for a page of fifty listings.
    const asked = openPool([{ user_id: "300000000000000001", name: "Nuisance" }]);

    await screen.findByText("Nuisance");

    await waitFor(() => expect(asked).toHaveLength(1));
    expect(asked[0]?.getAll("id").sort()).toEqual([
      "200000000000000001",
      "300000000000000001",
    ]);
  });

  it("still draws the table when names cannot be fetched", async () => {
    // Nothing on the page depends on a name. Losing the resolution costs recognition and
    // nothing else, so it must not take the listings with it.
    server.use(
      get("/auth/me", SIGNED_IN),
      get("/pools/spam", POOL),
      get("/pools/spam/listings", { listings: [LISTING], next_after_id: null, total: 1 }),
      get("/users/names", { detail: "no" }, 500),
    );

    renderWithQuery(<PoolDetail name="spam" />);

    expect(await screen.findByText("300000000000000001")).toBeInTheDocument();
    expect(screen.getByText("ban evasion")).toBeInTheDocument();
  });
});
