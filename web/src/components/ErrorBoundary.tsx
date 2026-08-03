import { Component } from "react";

import { reportError } from "@/api/report";
import { Button } from "@/components/ui";

import type { ErrorInfo, ReactNode } from "react";

/**
 * The last thing between a thrown render and a blank page.
 *
 * React unmounts the whole tree when a render throws and nothing catches it, so the
 * symptom a user reports is "it went white" — with the actual `TypeError` sitting in a
 * console they have already closed. This catches it, says so on screen, and posts it to
 * `/api/client-logs` so the stack reaches the log file on the host.
 *
 * A class, because `componentDidCatch` has no hook equivalent — this is the one thing in
 * the app React still has no function-component API for.
 *
 * Wraps the router rather than each route. A crash in the frame itself would escape a
 * per-route boundary, and the frame is where the session lives.
 */

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  override state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  override componentDidCatch(error: Error, info: ErrorInfo) {
    // `info.componentStack` names the components on the way down, which the raw stack of
    // a minified bundle does not. It is usually the fastest route to the broken file.
    const stack = [error.stack, info.componentStack].filter(Boolean).join("\n\n");
    reportError(Object.assign(new Error(error.message), { name: error.name, stack }), "render");
  }

  override render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    return (
      <main className="mx-auto max-w-lg p-8">
        <div className="rounded-md border border-danger/40 bg-danger/10 p-4 text-sm">
          <p className="font-medium text-surface-ink">This screen stopped working.</p>
          <p className="mt-1 text-surface-muted">
            The error has been recorded. Reloading usually clears it; if it does not, the
            details are in the server logs.
          </p>
          <p className="mt-3 font-mono text-xs break-words text-surface-muted">
            {error.name}: {error.message}
          </p>
          <Button
            variant="secondary"
            className="mt-4"
            onClick={() => {
              location.reload();
            }}
          >
            Reload
          </Button>
        </div>
      </main>
    );
  }
}
