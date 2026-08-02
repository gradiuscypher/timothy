import "@testing-library/jest-dom/vitest";

// TanStack Router restores scroll position on navigation, and jsdom has no scrolling to
// restore. Without this every route change prints a "not implemented" stack trace that
// looks like a failure and is not one.
window.scrollTo = () => {};
