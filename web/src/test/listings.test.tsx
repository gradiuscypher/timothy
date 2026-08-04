import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { PoolDetail } from "@/routes/PoolDetail";

import { SIGNED_IN, apiUrl, get, mockApi, renderWithQuery, server } from "./harness";

mockApi();

const POOL = {
  id: 1,
  name: "spam",
  description: "spammers",
  created_by: "user:200000000000000001",
  created_at: "2026-08-01T00:00:00Z",
};

function listing(id: number, userId: string, reason = "ban evasion") {
  return {
    id,
    pool_id: 1,
    pool_name: "spam",
    user_id: userId,
    reason,
    created_by: "user:200000000000000001",
    created_at: "2026-08-01T00:00:00Z",
  };
}

/** Records what the table asked for, and answers with whatever the test set up. */
function listingsEndpoint(pages: Record<string, unknown>) {
  const asked: URLSearchParams[] = [];
  const handler = http.get(apiUrl("/pools/spam/listings"), ({ request }) => {
    const query = new URL(request.url).searchParams;
    asked.push(query);
    const key = `${query.get("q") ?? ""}|${query.get("after_id") ?? ""}`;
    return HttpResponse.json(pages[key] ?? { listings: [], next_after_id: null, total: 0 });
  });
  return { handler, asked };
}

describe("the listing table", () => {
  it("shows how many are listed, not just the page", async () => {
    const { handler } = listingsEndpoint({
      "|": { listings: [listing(1, "1000")], next_after_id: 1, total: 3076 },
    });
    server.use(get("/auth/me", SIGNED_IN), get("/pools/spam", POOL), handler);

    renderWithQuery(<PoolDetail name="spam" />);

    expect(await screen.findByText("3076 listed")).toBeInTheDocument();
  });

  it("keeps password managers out of every field", async () => {
    // None of these is a credential, and an extension offering to save "ban evasion" as a
    // password is both wrong and in the way. `autocomplete` alone does not stop them.
    const { handler } = listingsEndpoint({
      "|": { listings: [listing(1, "1000")], next_after_id: null, total: 1 },
    });
    server.use(get("/auth/me", SIGNED_IN), get("/pools/spam", POOL), handler);

    renderWithQuery(<PoolDetail name="spam" />);
    await screen.findByText("1000");

    const fields = screen.getAllByRole("textbox").concat(screen.getAllByRole("searchbox"));
    expect(fields.length).toBeGreaterThan(0);
    for (const field of fields) {
      expect(field).toHaveAttribute("autocomplete", "off");
      expect(field).toHaveAttribute("data-1p-ignore");
      expect(field).toHaveAttribute("data-lpignore", "true");
    }
  });

  it("searches the backend rather than filtering what is on screen", async () => {
    // The page is 50 of several thousand rows. Filtering here would search the 50.
    const { handler, asked } = listingsEndpoint({
      "|": { listings: [listing(1, "1000")], next_after_id: null, total: 1 },
      "evasion|": { listings: [listing(2, "2000")], next_after_id: null, total: 1 },
    });
    server.use(get("/auth/me", SIGNED_IN), get("/pools/spam", POOL), handler);

    const { user } = renderWithQuery(<PoolDetail name="spam" />);
    await screen.findByText("1000");

    await user.type(screen.getByRole("searchbox"), "evasion");

    await waitFor(() => expect(screen.getByText("2000")).toBeInTheDocument());
    expect(asked.at(-1)?.get("q")).toBe("evasion");
  });

  it("pages with the cursor the backend handed back", async () => {
    const { handler, asked } = listingsEndpoint({
      "|": { listings: [listing(1, "1000")], next_after_id: 1, total: 2 },
      "|1": { listings: [listing(2, "2000")], next_after_id: null, total: 2 },
    });
    server.use(get("/auth/me", SIGNED_IN), get("/pools/spam", POOL), handler);

    const { user } = renderWithQuery(<PoolDetail name="spam" />);
    await screen.findByText("1000");

    await user.click(screen.getByRole("button", { name: "Next" }));

    await waitFor(() => expect(screen.getByText("2000")).toBeInTheDocument());
    expect(asked.at(-1)?.get("after_id")).toBe("1");
  });

  it("starts a new search from the first page", async () => {
    // Keeping the cursor would start the new search in the middle of results that do not
    // exist.
    const { handler, asked } = listingsEndpoint({
      "|": { listings: [listing(1, "1000")], next_after_id: 1, total: 2 },
      "|1": { listings: [listing(2, "2000")], next_after_id: null, total: 2 },
      "raid|": { listings: [], next_after_id: null, total: 0 },
    });
    server.use(get("/auth/me", SIGNED_IN), get("/pools/spam", POOL), handler);

    const { user } = renderWithQuery(<PoolDetail name="spam" />);
    await screen.findByText("1000");
    await user.click(screen.getByRole("button", { name: "Next" }));
    await screen.findByText("2000");

    await user.type(screen.getByRole("searchbox"), "raid");

    await waitFor(() => expect(asked.at(-1)?.get("q")).toBe("raid"));
    expect(asked.at(-1)?.get("after_id")).toBeNull();
  });

  it("says plainly when a search matched nothing", async () => {
    const { handler } = listingsEndpoint({
      "|": { listings: [listing(1, "1000")], next_after_id: null, total: 1 },
    });
    server.use(get("/auth/me", SIGNED_IN), get("/pools/spam", POOL), handler);

    const { user } = renderWithQuery(<PoolDetail name="spam" />);
    await screen.findByText("1000");

    await user.type(screen.getByRole("searchbox"), "nobody");

    expect(await screen.findByText(/Nothing matching/)).toBeInTheDocument();
  });
});

describe("removing a listing", () => {
  async function openTable() {
    const { handler } = listingsEndpoint({
      "|": { listings: [listing(1, "1000")], next_after_id: null, total: 1 },
    });
    server.use(get("/auth/me", SIGNED_IN), get("/pools/spam", POOL), handler);
    const rendered = renderWithQuery(<PoolDetail name="spam" />);
    await screen.findByText("1000");
    return rendered;
  }

  it("asks before it does anything", async () => {
    const { user } = await openTable();

    await user.click(screen.getAllByRole("button", { name: "Remove" })[0]!);

    expect(screen.getByRole("dialog")).toHaveTextContent(/bans already issued stay/i);
  });

  it("spells out that reverting reaches servers this screen is not showing", async () => {
    // `?revert=true` has no slash command on purpose. This is where somebody agrees to
    // it, so the dialog has to say what it does.
    const { user } = await openTable();

    await user.click(screen.getAllByRole("button", { name: /Remove & unban/ })[0]!);

    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent(/unban this user in every server/i);
    expect(dialog).toHaveTextContent(/Bans a server placed itself are never touched/i);
  });

  it("sends revert=false unless it was asked for", async () => {
    const asked: string[] = [];
    server.use(
      http.delete(apiUrl("/pools/spam/listings/1000"), ({ request }) => {
        asked.push(new URL(request.url).searchParams.get("revert") ?? "");
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const { user } = await openTable();

    await user.click(screen.getAllByRole("button", { name: "Remove" })[0]!);
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Remove" }));

    await waitFor(() => expect(asked).toEqual(["false"]));
  });

  it("does nothing at all if the dialog is cancelled", async () => {
    server.use(
      http.delete(apiUrl("/pools/spam/listings/1000"), () => {
        throw new Error("the listing was deleted after a cancel");
      }),
    );
    const { user } = await openTable();

    await user.click(screen.getAllByRole("button", { name: "Remove" })[0]!);
    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("bulk listing", () => {
  async function openPool() {
    const { handler } = listingsEndpoint({
      "|": { listings: [], next_after_id: null, total: 0 },
    });
    server.use(get("/auth/me", SIGNED_IN), get("/pools/spam", POOL), handler);
    const rendered = renderWithQuery(<PoolDetail name="spam" />);
    await screen.findByText(/Nobody is listed/);
    return rendered;
  }

  it("finds the IDs in anything that was pasted", async () => {
    // Moderators paste mentions, spreadsheet columns and Discord's own copied lists.
    const { user } = await openPool();

    await user.type(
      within(screen.getByRole("region", { name: "Add many" })).getByLabelText("User IDs"),
      "242024455190577152, <@110373943822540800>{enter}242024455190577152",
    );

    expect(
      await screen.findByRole("button", { name: /List 2 users/ }),
    ).toBeInTheDocument();
  });

  it("will not send until there is a reason", async () => {
    // The reason is what a moderator reading `/get_user_bans` in two years sees.
    const { user } = await openPool();

    await user.type(within(screen.getByRole("region", { name: "Add many" })).getByLabelText("User IDs"), "1000");

    expect(screen.getByRole("button", { name: /List 1 user/ })).toBeDisabled();
  });

  it("warns that a batch will trip the safety limit", async () => {
    const { user } = await openPool();
    await user.type(within(screen.getByRole("region", { name: "Add many" })).getByLabelText("Reason"), "raid");
    await user.type(within(screen.getByRole("region", { name: "Add many" })).getByLabelText("User IDs"), "1000 1001");

    await user.click(screen.getByRole("button", { name: /List 2 users/ }));

    expect(screen.getByRole("dialog")).toHaveTextContent(/trip the per-server safety limit/);
  });

  it("reports what was applied and what was already there", async () => {
    server.use(
      http.post(apiUrl("/pools/spam/listings/bulk"), () =>
        HttpResponse.json({ applied: ["1000"], skipped: ["1001"] }),
      ),
    );
    const { user } = await openPool();
    await user.type(within(screen.getByRole("region", { name: "Add many" })).getByLabelText("Reason"), "raid");
    await user.type(within(screen.getByRole("region", { name: "Add many" })).getByLabelText("User IDs"), "1000 1001");
    await user.click(screen.getByRole("button", { name: /List 2 users/ }));

    await user.click(screen.getByRole("button", { name: "List them" }));

    expect(await screen.findByText("1 applied")).toBeInTheDocument();
    expect(screen.getByText(/1 skipped/)).toBeInTheDocument();
  });

  it("shows the backend's refusal rather than swallowing it", async () => {
    server.use(
      http.post(apiUrl("/pools/spam/listings/bulk"), () =>
        HttpResponse.json({ detail: "not permitted: manage_listings" }, { status: 403 }),
      ),
    );
    const { user } = await openPool();
    await user.type(within(screen.getByRole("region", { name: "Add many" })).getByLabelText("Reason"), "raid");
    await user.type(within(screen.getByRole("region", { name: "Add many" })).getByLabelText("User IDs"), "1000");
    await user.click(screen.getByRole("button", { name: /List 1 user/ }));
    await user.click(screen.getByRole("button", { name: "List them" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "not permitted: manage_listings",
    );
  });
});
