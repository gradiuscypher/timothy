import { screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { SIGNED_IN, apiUrl, get, mockApi, renderApp, server } from "./harness";

mockApi();

/**
 * Reaching a user from what somebody actually remembers about them.
 *
 * The lookup is keyed by snowflake because everything downstream is, but a moderator
 * asking "why is this person banned" has a name and not eighteen digits. The one box
 * takes either, and which it took has to be unambiguous: a name resolves to candidates
 * to pick from, an ID goes straight through.
 */

const NUISANCE = "300000000000000001";

/** The app on the lookup page, recording what the name search was asked for. */
function openLookup(matches: Array<{ user_id: string; name: string }>) {
  const asked: string[] = [];
  server.use(
    get("/auth/me", SIGNED_IN),
    get(`/users/${NUISANCE}/listings`, []),
    http.get(apiUrl("/users/search"), ({ request }) => {
      asked.push(new URL(request.url).searchParams.get("q") ?? "");
      return HttpResponse.json(
        matches.map((match) => ({ ...match, observed_at: "2026-08-01T00:00:00Z" })),
      );
    }),
  );
  return { asked, ...renderApp("/users") };
}

async function look(
  user: ReturnType<typeof renderApp>["user"],
  typed: string,
): Promise<void> {
  const box = await screen.findByLabelText(/Discord user ID or name/);
  await user.clear(box);
  await user.type(box, typed);
  await user.click(screen.getByRole("button", { name: "Look up" }));
}

describe("looking a user up by name", () => {
  it("offers the users Timothy knows by that name", async () => {
    const { user } = openLookup([{ user_id: NUISANCE, name: "Nuisance" }]);

    await look(user, "Nuisance");

    expect(await screen.findByRole("link", { name: "Nuisance" })).toBeInTheDocument();
    // The ID is shown beside the name and not instead of it. Picking between two people
    // called the same thing is the reader's job, and the ID is what they pick on.
    expect(screen.getByText(NUISANCE)).toBeInTheDocument();
  });

  it("shows every candidate rather than guessing between them", async () => {
    const { user } = openLookup([
      { user_id: NUISANCE, name: "Nuisance" },
      { user_id: "300000000000000002", name: "Nuisance the second" },
    ]);

    await look(user, "Nuisance");

    expect(await screen.findByRole("link", { name: "Nuisance" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Nuisance the second" })).toBeInTheDocument();
  });

  it("says plainly when nobody Timothy has seen is called that", async () => {
    const { user } = openLookup([]);

    await look(user, "Nuisance");

    expect(await screen.findByText(/Nobody Timothy has seen/)).toBeInTheDocument();
  });

  it("goes straight to the user when an ID is typed, without searching", async () => {
    const { asked, user } = openLookup([]);

    await look(user, NUISANCE);

    expect(await screen.findByText(/Not listed on any pool/)).toBeInTheDocument();
    expect(asked).toEqual([]);
  });

  it("takes a pasted mention as the ID inside it", async () => {
    const { asked, user } = openLookup([]);

    await look(user, `<@${NUISANCE}>`);

    expect(await screen.findByText(/Not listed on any pool/)).toBeInTheDocument();
    expect(asked).toEqual([]);
  });

  it("searches for a name that merely contains digits", async () => {
    // The reason the ID pattern is anchored: unanchored, this would be read as a lookup
    // of user 2024, which is a different person and a silently wrong answer.
    const { asked, user } = openLookup([]);

    await look(user, "Nuisance2024");

    await waitFor(() => expect(asked).toEqual(["Nuisance2024"]));
  });
});
