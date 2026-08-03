import { RouterProvider, createMemoryHistory } from "@tanstack/react-router";
import { fireEvent, screen } from "@testing-library/react";
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

/**
 * Choose an option the way a browser does, rather than the way `user-event` does.
 *
 * `user-event` clicks a label by moving focus straight onto its control, so the settings
 * button's blur always names where focus went. A browser does not: pressing on a label —
 * which is not focusable, and whose radio is visually hidden — takes focus off the button
 * and gives it to nobody, so `relatedTarget` is null, and that arrives *before* the click.
 * That gap is the whole of the regression this file now guards, and no amount of
 * `user-event` clicking can see it.
 */
function pressWithMouse(option: HTMLElement) {
  const label = option.closest("label");
  if (!label) throw new Error("a choice has to be pressable by its label");
  const focused = document.activeElement;

  fireEvent.pointerDown(label);
  if (focused instanceof HTMLElement) fireEvent.focusOut(focused, { relatedTarget: null });
  // jsdom forwards a click on a label to the control it names, which is the half of this
  // that a browser also does.
  fireEvent.click(label);
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

  it("stays open while a choice inside it is being pressed", async () => {
    // The regression: a press on a label takes focus off the settings button and gives it
    // to nothing, and closing on that unmounted the panel before the click arrived.
    const { user } = renderApp();
    await openSettings(user);

    fireEvent.pointerDown(screen.getByRole("radio", { name: "Industrial" }).closest("label")!);
    fireEvent.focusOut(document.activeElement!, { relatedTarget: null });

    expect(screen.getByRole("group", { name: "Settings" })).toBeInTheDocument();
  });

  it("closes when focus is taken out of the panel by the keyboard", async () => {
    // The case the blur handler is for, and the one that has to survive the fix above:
    // tabbing away names where focus went, so the panel can tell it has been left.
    const { user } = renderApp();
    await openSettings(user);

    fireEvent.focusOut(document.activeElement!, {
      relatedTarget: screen.getByRole("link", { name: "Home" }),
    });

    expect(screen.queryByRole("group", { name: "Settings" })).not.toBeInTheDocument();
  });
});

/**
 * The same choices, made with a mouse rather than with `user-event`'s idealised clicking.
 *
 * Every assertion here has a counterpart above that passed throughout the whole time the
 * menu was unusable in a browser. The difference is `pressWithMouse`, and it is the only
 * thing standing between this suite and shipping a settings menu nobody can use again.
 */
describe("choosing a theme with a mouse", () => {
  it("applies a family, and leaves the menu open to show it", async () => {
    const { user } = renderApp();
    await openSettings(user);

    pressWithMouse(screen.getByRole("radio", { name: "Industrial" }));

    expect(stamped().family).toBe("industrial");
    expect(screen.getByRole("group", { name: "Settings" })).toBeInTheDocument();
  });

  it("applies a mode", async () => {
    const { user } = renderApp();
    await openSettings(user);

    pressWithMouse(screen.getByRole("radio", { name: "Dark" }));

    expect(stamped()).toEqual({ family: "default", mode: "dark" });
  });

  it("takes both choices in one visit to the menu", async () => {
    // Two presses in a row is where a panel that closes on the first one shows up as
    // "the theme half-changed", so the second choice has to be reachable without
    // re-opening anything.
    const { user } = renderApp();
    await openSettings(user);

    pressWithMouse(screen.getByRole("radio", { name: "Industrial" }));
    pressWithMouse(screen.getByRole("radio", { name: "Dark" }));

    expect(stamped()).toEqual({ family: "industrial", mode: "dark" });
    expect(localStorage.getItem(FAMILY_KEY)).toBe("industrial");
    expect(localStorage.getItem(MODE_KEY)).toBe("dark");
  });
});
