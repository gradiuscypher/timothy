import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { api, ApiError } from "@/api/client";

import { apiUrl, mockApi, server } from "./harness";

mockApi();

describe("the API client", () => {
  it("hands back the backend's own explanation", async () => {
    // "no such pool: spma" is a better answer for a moderator than "404", and the
    // backend writes these to be read.
    server.use(
      http.get(apiUrl("/pools/spma"), () =>
        HttpResponse.json({ detail: "no such pool: spma" }, { status: 404 }),
      ),
    );

    await expect(
      api.GET("/pools/{name}", { params: { path: { name: "spma" } } }),
    ).rejects.toThrow("no such pool: spma");
  });

  it("carries the status so a 401 can be told from a 403", async () => {
    server.use(
      http.get(apiUrl("/auth/me"), () =>
        HttpResponse.json({ detail: "no credentials" }, { status: 401 }),
      ),
    );

    const refused = await api.GET("/auth/me").catch((error: unknown) => error);

    expect(refused).toBeInstanceOf(ApiError);
    expect((refused as ApiError).isUnauthenticated).toBe(true);
  });

  it("says something usable when the refusal is not a sentence", async () => {
    // FastAPI's validation errors have a list of objects for `detail`, which is not
    // something to put in front of somebody.
    server.use(
      http.post(apiUrl("/pools"), () =>
        HttpResponse.json({ detail: [{ loc: ["body", "name"] }] }, { status: 422 }),
      ),
    );

    await expect(api.POST("/pools", { body: { name: "" } })).rejects.toThrow(
      "the backend answered 422",
    );
  });

  it("sends the session cookie and never a bearer token", async () => {
    // The browser's whole credential is the cookie. A token in the SPA bundle would be
    // the internal token in everybody's devtools.
    let authorization: string | null = "unset";
    server.use(
      http.get(apiUrl("/pools"), ({ request }) => {
        authorization = request.headers.get("authorization");
        return HttpResponse.json([]);
      }),
    );

    await api.GET("/pools");

    expect(authorization).toBeNull();
  });
});
