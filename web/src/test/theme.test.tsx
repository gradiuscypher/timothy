import { RouterProvider, createMemoryHistory } from "@tanstack/react-router";
import { screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { UserEvent } from "@testing-library/user-event";

import { FAMILY_KEY, MODE_KEY } from "@/components/theme";
import { makeRouter } from "@/router";

import { SIGNED_IN, get, mockApi, renderWithQuery, server } from "./harness";

mockApi();

function renderApp() {
  const router = makeRouter();
  router.history = createMemoryHistory({ initialEntries: ["/"] });
  return renderWithQuery(<RouterProvider router={router} />);
}

/** The theme lives behind the settings menu, so every choice starts by opening it. */
async function openSettings(user: UserEvent) {
  await user.click(await screen.findByRole("button", { name: "Settings" }));
  return screen.findByRole("group", { name: "Settings" });
}

/** Wait for the shell to be on screen without touching the settings menu. */
function shellRendered() {
  return screen.findByRole("button", { name: "Settings" });
}

/** What `<html>` is actually carrying, which is the only thing the stylesheet reads. */
function stamped() {
  const root = document.documentElement;
  return { family: root.dataset.family, mode: root.dataset.mode };
}

/** Pretend the operating system is asking for dark. */
function systemPrefersDark(dark: boolean) {
  window.matchMedia = (query: string) =>
    ({
      matches: dark && query.includes("dark"),
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    }) as unknown as MediaQueryList;
}

beforeEach(() => {
  localStorage.clear();
  systemPrefersDark(false);
  delete document.documentElement.dataset.family;
  delete document.documentElement.dataset.mode;
  server.use(get("/auth/me", SIGNED_IN), get("/guilds", []), get("/pools", []));
});

afterEach(() => {
  localStorage.clear();
});

describe("choosing a theme", () => {
  it("stamps the family and the resolved mode onto the document", async () => {
    renderApp();
    await shellRendered();

    expect(stamped()).toEqual({ family: "default", mode: "light" });
  });

  it("applies the stored theme without the menu ever being opened", async () => {
    // The picker is behind a button now; the theme must not be.
    localStorage.setItem(FAMILY_KEY, "industrial");
    localStorage.setItem(MODE_KEY, "dark");

    renderApp();
    await shellRendered();

    expect(stamped()).toEqual({ family: "industrial", mode: "dark" });
  });

  it("resolves 'system' against the operating system rather than storing a colour", async () => {
    systemPrefersDark(true);

    renderApp();
    await shellRendered();

    // The preference is "system"; the attribute is the answer to it. CSS cannot ask the
    // question, so nothing but `light` or `dark` may ever reach the stylesheet.
    expect(stamped().mode).toBe("dark");
    expect(localStorage.getItem(MODE_KEY)).toBeNull();
  });

  it("shows both choices at once, with the current one marked", async () => {
    const { user } = renderApp();
    await openSettings(user);

    expect(screen.getByRole("radio", { name: "Default" })).toBeChecked();
    expect(screen.getByRole("radio", { name: "Industrial" })).not.toBeChecked();
    expect(screen.getByRole("radio", { name: "System" })).toBeChecked();
  });

  it("applies a family the moment it is chosen", async () => {
    const { user } = renderApp();
    await openSettings(user);

    await user.click(screen.getByRole("radio", { name: "Industrial" }));

    expect(stamped().family).toBe("industrial");
  });

  it("applies a mode the moment it is chosen", async () => {
    const { user } = renderApp();
    await openSettings(user);

    await user.click(screen.getByRole("radio", { name: "Dark" }));

    expect(stamped()).toEqual({ family: "default", mode: "dark" });
  });

  it("remembers both choices for the next visit", async () => {
    const first = renderApp();
    await openSettings(first.user);
    await first.user.click(screen.getByRole("radio", { name: "Industrial" }));
    await first.user.click(screen.getByRole("radio", { name: "Dark" }));
    first.unmount();

    expect(localStorage.getItem(FAMILY_KEY)).toBe("industrial");
    expect(localStorage.getItem(MODE_KEY)).toBe("dark");

    // A second visit reads storage rather than starting from the default again.
    delete document.documentElement.dataset.family;
    delete document.documentElement.dataset.mode;
    renderApp();
    await shellRendered();

    expect(stamped()).toEqual({ family: "industrial", mode: "dark" });
  });

  it("sets color-scheme, which is what paints the scrollbars", async () => {
    const { user } = renderApp();
    await openSettings(user);

    await user.click(screen.getByRole("radio", { name: "Dark" }));

    expect(document.documentElement.style.colorScheme).toBe("dark");
  });

  it("keeps an explicit choice when the operating system disagrees", async () => {
    systemPrefersDark(true);
    localStorage.setItem(MODE_KEY, "light");

    renderApp();
    await shellRendered();

    expect(stamped().mode).toBe("light");
  });

  it("ignores a stored value that is not a theme", async () => {
    localStorage.setItem(FAMILY_KEY, "brutalist");
    localStorage.setItem(MODE_KEY, "chartreuse");

    renderApp();
    await shellRendered();

    expect(stamped()).toEqual({ family: "default", mode: "light" });
  });
});

describe("the settings menu", () => {
  it("keeps the theme out of the way until it is asked for", async () => {
    const { user } = renderApp();
    await shellRendered();

    expect(screen.queryByRole("radio", { name: "Industrial" })).not.toBeInTheDocument();

    await openSettings(user);
    expect(screen.getByRole("radio", { name: "Industrial" })).toBeInTheDocument();
  });

  it("closes on Escape, so an accidental open is not a trap", async () => {
    const { user } = renderApp();
    await openSettings(user);

    await user.keyboard("{Escape}");

    expect(screen.queryByRole("group", { name: "Settings" })).not.toBeInTheDocument();
  });

  it("closes when the click lands somewhere else", async () => {
    const { user } = renderApp();
    await openSettings(user);

    await user.click(screen.getByRole("link", { name: "Home" }));

    expect(screen.queryByRole("group", { name: "Settings" })).not.toBeInTheDocument();
  });
});
