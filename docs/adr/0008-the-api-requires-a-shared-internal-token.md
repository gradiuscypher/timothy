# The API requires a shared internal token

ADR 0003 says callers assert identity and the backend resolves authority. That is only
safe if the assertion itself is trusted, and phase 2 found it was not: nginx proxies
`/api` from what will be a public Cloudflare tunnel, so "I am acting for user 123" reaching
the backend unauthenticated is an *authority* assertion. Anyone who found the hostname
could name an administrator's user ID and be believed.

Every route but `/health` and `/openapi.json` therefore requires `TIMOTHY_INTERNAL_TOKEN`
as a bearer credential, separately from `X-Timothy-Actor`. The two headers answer two
different questions: the token says which *process* is calling, the actor header says
which *person* it is calling for. Conflating them is what this exists to prevent.

An unset token refuses every request rather than accepting every request. A missing
environment variable turning the API open is the failure this is meant to make impossible,
so it fails the other way.

## Consequences

- The bot container holds the token. So does phase 5's migration tool.
- A browser reaching `/api` gets a 401 unless it holds a session cookie. Phase 6 added
  that as the *second* credential rather than as an exception to this one: a browser
  never sees the internal token, and a session names its own actor, so the pairing this
  ADR is about — a credential plus an assertion the credential does not constrain —
  simply does not arise there. Sending `X-Timothy-Actor` with a session is refused.
  `/auth/login` and `/auth/callback` are outside both, because getting a credential is
  what they are for; neither is worth anything without completing Discord's consent
  screen.
- The token is a single shared secret with no rotation story. That is proportionate while
  the only holders are containers in one compose network; it would not be if the API were
  ever reachable by anything else.
- `Requirement.SYSTEM` operations — guild registration, gateway event relay — have no
  Discord authority behind them to check, so for those the token is the whole of the
  check. That is the sharpest edge here and the reason those operations are enumerated
  rather than implied.
