import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory } from "@tanstack/react-router";
import { render, type RenderResult } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse, type JsonBodyType, type RequestHandler } from "msw";
import { setupServer } from "msw/node";
import type { ReactElement } from "react";
import { afterAll, afterEach, beforeAll } from "vitest";

import { ApiError } from "@/api/client";
import { ToastProvider } from "@/components/Toast";
import { makeRouter } from "@/router";

/**
 * The API, intercepted at the network rather than at the module.
 *
 * These tests drive the real `openapi-fetch` client, the real React Query cache and the
 * real components; the only thing standing in is the backend, and it stands in as HTTP.
 * Mocking the hooks instead would test the components against a fiction, and the shape
 * of that fiction is exactly what a generated client is supposed to keep honest.
 */

/**
 * Handlers are absolute.
 *
 * The client anchors its base URL to the page's origin so that `fetch` has something to
 * resolve, and msw matches what the client actually sends — a relative pattern here
 * matches nothing, silently, and the request escapes to a port that is not listening.
 */
const pageOrigin = typeof location === "undefined" ? undefined : location.origin;

export const ORIGIN = pageOrigin ?? "http://localhost:3000";

export const apiUrl = (path: string): string => `${ORIGIN}/api${path}`;

export const server = setupServer(
  // Every screen that lists user IDs asks what they are called, and in most tests the
  // answer is "nothing known" — the ordinary state of the cache, and the one in which
  // the ID itself is what gets drawn. A default rather than a line in twenty tests, and
  // `resetHandlers` puts it back after any test that overrides it with real names.
  http.get(apiUrl("/users/names"), () => HttpResponse.json([])),
);

export function mockApi(): void {
  beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
  afterEach(() => server.resetHandlers());
  afterAll(() => server.close());
}

/** `GET /api/<path>` answers with `body`. */
export function get(path: string, body: JsonBodyType, status = 200): RequestHandler {
  return http.get(apiUrl(path), () => HttpResponse.json(body, { status }));
}

export function post(path: string, body: JsonBodyType, status = 200): RequestHandler {
  return http.post(apiUrl(path), () => HttpResponse.json(body, { status }));
}

export const SIGNED_IN = {
  actor: "user:200000000000000001",
  user_id: "200000000000000001",
  username: "gradius",
  avatar: null,
  expires_at: "2026-08-09T00:00:00Z",
  manages_pools: true,
  is_owner: false,
};

export const MEMBER = {
  ...SIGNED_IN,
  username: "member",
  manages_pools: false,
  is_owner: false,
};

/** Whoever runs the deployment. Deliberately not a pool owner — they are separate jobs. */
export const OWNER = {
  ...SIGNED_IN,
  username: "operator",
  manages_pools: false,
  is_owner: true,
};

/**
 * Render a component with a query cache that neither retries nor caches across tests.
 *
 * The providers are the ones `main.tsx` puts above the router, and they have to be: a
 * screen that confirms a save with `useToast` throws outright without one, so a harness
 * missing a provider does not fail the assertion, it fails the render.
 */
export function renderWithQuery(element: ReactElement): RenderResult & {
  user: ReturnType<typeof userEvent.setup>;
} {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: 0, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  return {
    user: userEvent.setup(),
    ...render(
      <QueryClientProvider client={client}>
        <ToastProvider>{element}</ToastProvider>
      </QueryClientProvider>,
    ),
  };
}

/**
 * The whole app, mounted at `path` on a history that is not the browser's.
 *
 * Screens that link anywhere need a router above them — `Link` reads one out of context
 * and cannot render without it — so anything with navigation in it is rendered through
 * here rather than on its own. What that costs is a session handler; what it buys is
 * that the route, the shell around it and the page are all the real ones.
 */
export function renderApp(path = "/"): ReturnType<typeof renderWithQuery> {
  const router = makeRouter();
  router.history = createMemoryHistory({ initialEntries: [path] });
  return renderWithQuery(<RouterProvider router={router} />);
}

export { ApiError };
