import "@testing-library/jest-dom/vitest";

// Almost every test file here runs in jsdom, and the two stand-ins below are for gaps in
// it. `styles.test.ts` is the exception: it compiles the real stylesheet, which means
// running esbuild, which cannot run inside jsdom — so it declares the node environment
// and arrives here with no `window` at all. Setup files run for every file regardless of
// environment, hence the guard.
if (typeof window !== "undefined") {
  // TanStack Router restores scroll position on navigation, and jsdom has no scrolling to
  // restore. Without this every route change prints a "not implemented" stack trace that
  // looks like a failure and is not one.
  window.scrollTo = () => {};

  // jsdom does not implement `matchMedia` at all — it is undefined rather than unhelpful.
  // The theme reads it to resolve "system", and while `theme.ts` copes with its absence,
  // coping means answering "light" for everybody, which would leave the dark half of
  // every theme untestable. This is the smallest stand-in that supports both: a query
  // that reports no match and accepts listeners nobody fires. A test that needs the OS to
  // say dark overrides this for itself.
  window.matchMedia = (query: string): MediaQueryList =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }) as MediaQueryList;
}
