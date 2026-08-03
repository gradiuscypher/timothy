import { render, screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { installGlobalErrorReporting, reportError } from "@/api/report";
import { ErrorBoundary } from "@/components/ErrorBoundary";

import { apiUrl, mockApi, server } from "./harness";

mockApi();

/** What the SPA posted, in order. */
function collect(): { sent: Array<Record<string, unknown>> } {
  const sent: Array<Record<string, unknown>> = [];
  server.use(
    http.post(apiUrl("/client-logs"), async ({ request }) => {
      sent.push((await request.json()) as Record<string, unknown>);
      return new HttpResponse(null, { status: 202 });
    }),
  );
  return { sent };
}

/** `reportError` deduplicates for the life of the module, so each test needs a new one. */
async function freshReporter() {
  vi.resetModules();
  return import("@/api/report");
}

describe("reporting the browser's own crashes", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("posts an unhandled error to the backend", async () => {
    const { sent } = collect();
    const { reportError: report } = await freshReporter();

    report(new TypeError("x is not a function"), "window");
    await vi.waitFor(() => expect(sent).toHaveLength(1));

    expect(sent[0]).toMatchObject({
      level: "error",
      message: "TypeError: x is not a function",
      kind: "window",
    });
    expect(sent[0]?.stack).toEqual(expect.stringContaining("TypeError"));
  });

  it("sends the same error only once", async () => {
    // A component that throws on every render throws identically on every render, and
    // the point of reporting is a record, not a flood.
    const { sent } = collect();
    const { reportError: report } = await freshReporter();

    report(new Error("boom"), "window");
    report(new Error("boom"), "window");
    report(new Error("boom"), "window");
    await vi.waitFor(() => expect(sent).toHaveLength(1));
  });

  it("describes a rejection that is not an Error at all", async () => {
    const { sent } = collect();
    const { reportError: report } = await freshReporter();

    report({ code: 42 }, "unhandledrejection");
    await vi.waitFor(() => expect(sent).toHaveLength(1));

    expect(sent[0]?.message).toBe('{"code":42}');
  });

  it("does not throw when the backend is unreachable", () => {
    // This runs because something is already broken. A reporter that can throw turns
    // one crash into a loop.
    server.use(http.post(apiUrl("/client-logs"), () => HttpResponse.error()));

    expect(() => {
      reportError(new Error("unreachable"), "window");
    }).not.toThrow();
  });

  it("catches a rejected promise nobody handled", async () => {
    const { sent } = collect();
    const uninstall = installGlobalErrorReporting();

    // jsdom has no `PromiseRejectionEvent` constructor, so the event is assembled by
    // hand. The listener only reads `reason`, which is what a browser would put there.
    const event = Object.assign(new Event("unhandledrejection"), {
      reason: new Error("nobody caught this"),
    });
    window.dispatchEvent(event);
    await vi.waitFor(() => expect(sent).toHaveLength(1));

    expect(sent[0]).toMatchObject({ kind: "unhandledrejection" });
    uninstall();
  });
});

describe("the error boundary", () => {
  function Broken(): never {
    throw new TypeError("render exploded");
  }

  it("shows a message instead of a blank page, and reports it", async () => {
    const { sent } = collect();
    // React logs the caught error itself; the test is about what the boundary does.
    vi.spyOn(console, "error").mockImplementation(() => undefined);

    render(
      <ErrorBoundary>
        <Broken />
      </ErrorBoundary>,
    );

    expect(screen.getByText("This screen stopped working.")).toBeInTheDocument();
    expect(screen.getByText(/render exploded/)).toBeInTheDocument();
    await vi.waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toMatchObject({ kind: "render" });
  });

  it("leaves a working tree alone", () => {
    render(
      <ErrorBoundary>
        <p>fine</p>
      </ErrorBoundary>,
    );

    expect(screen.getByText("fine")).toBeInTheDocument();
  });
});
