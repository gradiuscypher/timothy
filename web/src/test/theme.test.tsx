import { RouterProvider, createMemoryHistory } from "@tanstack/react-router";
import { screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { FAMILY_KEY, MODE_KEY } from "@/components/theme";
import { makeRouter } from "@/router";

import { SIGNED_IN, get, mockApi, renderWithQuery, server } from "./harness";

mockApi();

function renderApp() {
  const router = makeRouter();
  router.history = createMemoryHistory({ initialEntries: ["/"] });
  return renderWithQuery(<RouterProvider router={router} />);
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
    await screen.findByRole("combobox", { name: "Theme" });

    expect(stamped()).toEqual({ family: "default", mode: "light" });
  });

  it("resolves 'system' against the operating system rather than storing a colour", async () => {
    systemPrefersDark(true);

    renderApp();
    await screen.findByRole("combobox", { name: "Theme" });

    // The preference is "system"; the attribute is the answer to it. CSS cannot ask the
    // question, so nothing but `light` or `dark` may ever reach the stylesheet.
    expect(stamped().mode).toBe("dark");
    expect(localStorage.getItem(MODE_KEY)).toBeNull();
  });

  it("applies a family the moment it is chosen", async () => {
    const { user } = renderApp();
    const theme = await screen.findByRole("combobox", { name: "Theme" });

    await user.selectOptions(theme, "industrial");

    expect(stamped().family).toBe("industrial");
  });

  it("applies a mode the moment it is chosen", async () => {
    const { user } = renderApp();
    const mode = await screen.findByRole("combobox", { name: "Light or dark" });

    await user.selectOptions(mode, "dark");

    expect(stamped()).toEqual({ family: "default", mode: "dark" });
  });

  it("remembers both choices for the next visit", async () => {
    const first = renderApp();
    await first.user.selectOptions(
      await screen.findByRole("combobox", { name: "Theme" }),
      "industrial",
    );
    await first.user.selectOptions(
      await screen.findByRole("combobox", { name: "Light or dark" }),
      "dark",
    );
    first.unmount();

    expect(localStorage.getItem(FAMILY_KEY)).toBe("industrial");
    expect(localStorage.getItem(MODE_KEY)).toBe("dark");

    // A second visit reads storage rather than starting from the default again.
    delete document.documentElement.dataset.family;
    delete document.documentElement.dataset.mode;
    renderApp();
    await screen.findByRole("combobox", { name: "Theme" });

    expect(stamped()).toEqual({ family: "industrial", mode: "dark" });
  });

  it("sets color-scheme, which is what paints the scrollbars", async () => {
    const { user } = renderApp();

    await user.selectOptions(
      await screen.findByRole("combobox", { name: "Light or dark" }),
      "dark",
    );

    expect(document.documentElement.style.colorScheme).toBe("dark");
  });

  it("keeps an explicit choice when the operating system disagrees", async () => {
    systemPrefersDark(true);
    localStorage.setItem(MODE_KEY, "light");

    renderApp();
    await screen.findByRole("combobox", { name: "Theme" });

    expect(stamped().mode).toBe("light");
  });

  it("ignores a stored value that is not a theme", async () => {
    localStorage.setItem(FAMILY_KEY, "brutalist");
    localStorage.setItem(MODE_KEY, "chartreuse");

    renderApp();
    await screen.findByRole("combobox", { name: "Theme" });

    expect(stamped()).toEqual({ family: "default", mode: "light" });
  });
});
