import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { ApiError } from "@/api/client";
import { makeRouter } from "@/router";

import "./styles.css";

/**
 * One query client for the app.
 *
 * Retries are off for anything the backend refused. A 403 is an answer, not a hiccup,
 * and retrying it three times means three permission lookups against Discord for a
 * question already settled — which is the budget enforcement runs on. Transport failures
 * still get one more go.
 */
export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 15_000,
        refetchOnWindowFocus: false,
        retry: (attempt, error) => !(error instanceof ApiError) && attempt < 1,
      },
      mutations: { retry: false },
    },
  });
}

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <QueryClientProvider client={makeQueryClient()}>
        <RouterProvider router={makeRouter()} />
      </QueryClientProvider>
    </StrictMode>,
  );
}
