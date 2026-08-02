import { RouterProvider, createMemoryHistory } from "@tanstack/react-router";
import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { makeRouter } from "@/router";

import { MEMBER, OWNER, SIGNED_IN, get, mockApi, renderWithQuery, server } from "./harness";

mockApi();

function renderApp(path = "/") {
  const router = makeRouter();
  router.history = createMemoryHistory({ initialEntries: [path] });
  return renderWithQuery(<RouterProvider router={router} />);
}

describe("the shell", () => {
  it("asks an unauthenticated browser to sign in", async () => {
    // A 401 here is the ordinary first visit, not a failure.
    server.use(get("/auth/me", { detail: "no credentials" }, 401));

    renderApp();

    expect(await screen.findByText(/Sign in with Discord/)).toBeInTheDocument();
  });

  it("sends the browser to the backend to log in, never to Discord directly", async () => {
    // The client ID and the state cookie are the backend's business. A link built here
    // would be a second copy of the flow, out of step with the one that validates it.
    server.use(get("/auth/me", { detail: "no credentials" }, 401));

    renderApp();

    const link = await screen.findByRole("link", { name: /Sign in with Discord/ });
    expect(link).toHaveAttribute("href", "/api/auth/login");
  });

  it("says so when Discord refused the login", async () => {
    window.history.replaceState({}, "", "/?login=failed");
    server.use(get("/auth/me", { detail: "no credentials" }, 401));

    renderApp();

    expect(await screen.findByRole("alert")).toHaveTextContent(/Discord refused/);
    window.history.replaceState({}, "", "/");
  });

  it("shows the pool screens to somebody who owns pools", async () => {
    server.use(get("/auth/me", SIGNED_IN), get("/guilds", []), get("/pools", []));

    renderApp();

    expect(await screen.findByRole("link", { name: "Pools" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Audit log" })).toBeInTheDocument();
  });

  it("does not offer the operations view to somebody who merely owns pools", async () => {
    // Running the deployment and owning the pools are different jobs (ADR 0011). This is
    // the one place in the UI where that shows.
    server.use(get("/auth/me", SIGNED_IN), get("/guilds", []), get("/pools", []));

    renderApp();

    await screen.findByRole("link", { name: "Pools" });
    expect(screen.queryByRole("link", { name: "Operations" })).not.toBeInTheDocument();
  });

  it("offers it to whoever runs the deployment", async () => {
    server.use(get("/auth/me", OWNER), get("/guilds", []));

    renderApp();

    expect(await screen.findByRole("link", { name: "Operations" })).toBeInTheDocument();
    // Owning the deployment is not owning the pools, and the nav says so both ways.
    expect(screen.queryByRole("link", { name: "Pools" })).not.toBeInTheDocument();
  });

  it("hides them from somebody who does not", async () => {
    // A courtesy, not a gate — every route behind these resolves the permission again.
    server.use(get("/auth/me", MEMBER), get("/guilds", []));

    renderApp();

    await waitFor(() => expect(screen.getByText("member")).toBeInTheDocument());
    expect(screen.queryByRole("link", { name: "Pools" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Audit log" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Servers" })).toBeInTheDocument();
  });

  it("tells a member with no servers why the list is empty", async () => {
    server.use(get("/auth/me", MEMBER), get("/guilds", []));

    renderApp("/guilds");

    expect(await screen.findByText(/sign out and back in/)).toBeInTheDocument();
  });
});
