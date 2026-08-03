/**
 * Reporting the browser's own crashes to the backend, so they end up in the log file.
 *
 * A React render that throws happens on somebody's laptop. nginx logs the requests the
 * page made and knows nothing about the exception, so the only record was a devtools
 * console nobody had open. This posts those errors to `/api/client-logs`, which writes
 * them into the same file the backend and the bot write to.
 *
 * Written against `fetch` directly rather than through {@link api}. This runs *because*
 * something is already broken, and the typed client carries a middleware that throws on
 * a refusal — an error reporter that can itself throw is a reporter that turns one crash
 * into a loop. Nothing here rejects, ever.
 */

const ENDPOINT = "/api/client-logs";

/** Matches the backend's `MAX_STACK`. Longer stacks are refused, not truncated, there. */
const MAX_STACK = 16_000;
const MAX_MESSAGE = 2_000;

/** How it surfaced, which the exception itself never says. */
export type ErrorKind = "render" | "window" | "unhandledrejection";

interface Report {
  level: "error";
  message: string;
  stack?: string;
  url?: string;
  kind?: ErrorKind;
}

/**
 * Errors already reported this page load.
 *
 * A component that throws on every render throws identically on every render, and a
 * broken WebSocket rejects on a timer forever. The backend caps this per minute too —
 * this is the cheaper half of the same guard, and the half that keeps a user's own
 * network from carrying the same line a thousand times.
 */
const seen = new Set<string>();
const MAX_DISTINCT = 20;

function describe(error: unknown): { message: string; stack?: string } {
  if (error instanceof Error) {
    return { message: `${error.name}: ${error.message}`, stack: error.stack };
  }
  // A rejected promise can carry anything at all, including a string or a DOM event.
  // `JSON.stringify` is declared as returning a string but answers `undefined` for a
  // function or a bare `undefined`, both of which a rejection can carry — so `String`
  // rather than the stringify, which cannot be undefined and describes those two just
  // as well.
  if (typeof error === "string") return { message: error };
  return { message: JSON.stringify(error) || String(error) };
}

/**
 * Send one error to the backend. Never throws and never rejects.
 *
 * `keepalive` because an error thrown during navigation would otherwise be cancelled
 * along with the page that was leaving — which is exactly when the interesting ones
 * happen.
 */
export function reportError(error: unknown, kind: ErrorKind): void {
  try {
    const { message, stack } = describe(error);
    if (!message) return;

    const fingerprint = `${kind}:${message}`;
    if (seen.has(fingerprint) || seen.size >= MAX_DISTINCT) return;
    seen.add(fingerprint);

    const body: Report = {
      level: "error",
      message: message.slice(0, MAX_MESSAGE),
      stack: stack?.slice(0, MAX_STACK),
      url: typeof location === "undefined" ? undefined : location.href,
      kind,
    };

    void fetch(ENDPOINT, {
      method: "POST",
      credentials: "same-origin",
      keepalive: true,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).catch(() => {
      // The backend is unreachable, or the session has expired. Either way there is
      // nowhere left to report it to, and the console still has it.
    });
  } catch {
    // Reporting a crash must not be able to cause one.
  }
}

/**
 * Catch the errors no component boundary sees: a listener that threw, a promise nobody
 * attached a `catch` to.
 *
 * Called once at startup. Returns a teardown so tests can install and remove it.
 */
export function installGlobalErrorReporting(): () => void {
  const onError = (event: ErrorEvent) => reportError(event.error ?? event.message, "window");
  const onRejection = (event: PromiseRejectionEvent) =>
    reportError(event.reason, "unhandledrejection");

  window.addEventListener("error", onError);
  window.addEventListener("unhandledrejection", onRejection);
  return () => {
    window.removeEventListener("error", onError);
    window.removeEventListener("unhandledrejection", onRejection);
  };
}
