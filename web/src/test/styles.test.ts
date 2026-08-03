// @vitest-environment node

import { fileURLToPath } from "node:url";

import { build, type Rollup } from "vite";
import { beforeAll, describe, expect, it } from "vitest";

/**
 * The themes, checked against the stylesheet Tailwind actually emits.
 *
 * Every other test in this suite runs under `css: false` and cannot see a colour, which
 * is precisely how this codebase shipped a dark-only app while `styles.css` appeared to
 * define a light palette too: `@theme` nested inside `@media (prefers-color-scheme: dark)`
 * is not scoped to it — Tailwind hoists the declarations and the last one silently wins.
 * The source looked correct, no test could see it, and nothing failed. Reading the
 * compiled output is the only thing that would have caught it, so that is what this does.
 */

const WEB_ROOT = fileURLToPath(new URL("../..", import.meta.url));

let css = "";

beforeAll(async () => {
  // `write: false` keeps the whole build in memory: no temp directory to clean up, and
  // no chance of reading a `dist/` some earlier command left behind.
  const result = (await build({
    root: WEB_ROOT,
    logLevel: "silent",
    build: { write: false },
  })) as Rollup.RollupOutput;

  const stylesheets = result.output.filter(
    (chunk): chunk is Rollup.OutputAsset =>
      chunk.type === "asset" && chunk.fileName.endsWith(".css"),
  );
  const [sheet] = stylesheets;
  if (!sheet) throw new Error("the build produced no stylesheet to check");
  // The minifier drops the quotes from attribute selectors, so `[data-mode="dark"]` in
  // the source is `[data-mode=dark]` here. Normalising rather than matching both keeps
  // the assertions below readable as the selectors somebody actually wrote.
  css = String(sheet.source).replace(/\[([\w-]+)="([^"]*)"\]/g, "[$1=$2]");
}, 120_000);

/** Every declaration of `name` inside the block for `selector`. */
function tokens(selector: string): Record<string, string> {
  const start = css.indexOf(selector);
  expect(start, `${selector} is not in the built stylesheet`).toBeGreaterThanOrEqual(0);
  const block = css.slice(css.indexOf("{", start) + 1, css.indexOf("}", start));
  const declared: Record<string, string> = {};
  for (const [, key, value] of block.matchAll(/(--[\w-]+):([^;]+)/g)) {
    if (key && value) declared[key] = value.trim();
  }
  return declared;
}

const THEMES = [
  "html[data-family=default][data-mode=light]",
  "html[data-family=default][data-mode=dark]",
  "html[data-family=industrial][data-mode=light]",
  "html[data-family=industrial][data-mode=dark]",
] as const;

describe("the built stylesheet", () => {
  it("carries all four themes", () => {
    for (const selector of THEMES) {
      expect(css).toContain(selector);
    }
  });

  it("gives each theme its own surfaces and its own accent", () => {
    const surfaces = new Set<string>();
    const accents = new Set<string>();

    for (const selector of THEMES) {
      const declared = tokens(selector);
      const surface = declared["--color-surface-0"] ?? "";
      const accent = declared["--color-accent"] ?? "";
      // The regression this file exists for: a theme that resolves to somebody else's
      // colours because its block was hoisted away.
      expect(surface, selector).toBeTruthy();
      expect(accent, selector).toBeTruthy();
      surfaces.add(surface);
      accents.add(accent);
    }

    expect(surfaces.size).toBe(THEMES.length);
    expect(accents.size).toBe(THEMES.length);
  });

  it("tells the browser which way each theme runs", () => {
    for (const selector of THEMES) {
      const expected = selector.includes("data-mode=dark") ? "dark" : "light";
      const start = css.indexOf(selector);
      const block = css.slice(start, css.indexOf("}", start));
      expect(block, selector).toContain(`color-scheme:${expected}`);
    }
  });

  it("squares every corner in the industrial family without touching a component", () => {
    // `rounded-md` and friends all read a `--radius-*` custom property, so overriding
    // those reaches route JSX this stylesheet has never heard of. If Tailwind ever stops
    // emitting radius that way, the industrial theme goes quietly round again.
    expect(css).toContain(".rounded-md{border-radius:var(--radius-md)}");

    const industrial = tokens("html[data-family=industrial]{");
    expect(industrial["--radius-md"]).toBe("0");
    expect(industrial["--radius-lg"]).toBe("0");
  });

  it("puts Berkeley Mono in front of a face that is actually committed", () => {
    const industrial = tokens("html[data-family=industrial]{");
    const stack = industrial["--font-body"] ?? "";

    expect(stack.indexOf("Berkeley Mono")).toBeGreaterThanOrEqual(0);
    // Order is the whole fallback mechanism: the licensed face is absent everywhere but
    // one host, and what follows it is what nearly everyone sees.
    expect(stack.indexOf("Berkeley Mono")).toBeLessThan(stack.indexOf("IBM Plex Mono"));
    expect(css).toContain("/fonts/berkeley/BerkeleyMono-Regular.woff2");
    expect(css).toContain("/fonts/plex/IBMPlexMono-Regular-latin.woff2");
  });

  it("leaves the default family on the fonts it has always used", () => {
    const light = tokens(THEMES[0]);
    const dark = tokens(THEMES[1]);

    expect(light["--font-body"]).toBeUndefined();
    expect(dark["--font-body"]).toBeUndefined();
    expect(light["--font-mono"]).toBeUndefined();
  });
});
