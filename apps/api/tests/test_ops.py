"""The operator's view of Timothy itself.

Most of this is arithmetic, and arithmetic is not what these tests are for. What they
pin is the handful of places where an obvious-looking number would be the wrong number:
which table a per-day count comes from, what a dry-run row means, and what a `failed`
job is as opposed to a `failed` outcome.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2
from fastapi.testclient import TestClient

from timothy_api.jobs import JobKind
from timothy_api.settings import Settings
from timothy_core.ports.discord import ForbiddenError
from timothy_core.ports.fake import FakeDiscord

from .conftest import (
    CHANNEL,
    GUILD,
    GUILD_ADMIN,
    LISTED_USER,
    MEMBER,
    POOL_ADMIN,
    Enforcement,
    headers,
    insert_job,
)


def at(when: datetime) -> Callable[[], datetime]:
    """A clock stuck at one moment, so backoff can be stepped over rather than waited on."""
    return lambda: when


def worker(
    client: TestClient, settings: Settings, discord: FakeDiscord, *, dry_run: bool
) -> Enforcement:
    """The worker with dry run forced either way, sharing the application's own registry.

    Not the `enforcement` fixture, because these tests need both modes in one test: the
    setup runs for real to clear the queue, and the part under test runs in dry run.
    """
    return Enforcement(
        settings.model_copy(update={"dry_run": dry_run}),
        discord,
        client.app.state.self_unbans,  # ty: ignore[unresolved-attribute]
    )


def _overview(client: TestClient, query: str = "") -> Any:  # noqa: ANN401 — the JSON
    response = client.get(f"/ops/overview{query}", headers=headers(POOL_ADMIN))
    assert response.status_code == 200, response.text
    return response.json()


def _activity(client: TestClient, query: str = "") -> dict[str, int]:
    response = client.get(f"/ops/activity{query}", headers=headers(POOL_ADMIN))
    assert response.status_code == 200, response.text
    return {point["series"]: point["count"] for point in response.json()}


def _subscribe(client: TestClient, level: str = "ban") -> httpx2.Response:
    return client.put(
        f"/guilds/{GUILD}/subscriptions/spam",
        json={"level": level},
        headers=headers(GUILD_ADMIN),
    )


def _list(client: TestClient, user_id: int = LISTED_USER) -> httpx2.Response:
    return client.post(
        "/pools/spam/listings",
        json={"user_id": str(user_id), "reason": "ban evasion"},
        headers=headers(POOL_ADMIN),
    )


# -- who may look ----------------------------------------------------------------------


def test_the_ops_view_is_for_the_management_guilds_administrators(pool: TestClient) -> None:
    """The same gate as the audit log. A configured list of owner IDs would be the first
    authority Timothy stored rather than derived (ADR 0001)."""
    assert pool.get("/ops/overview", headers=headers(POOL_ADMIN)).status_code == 200


def test_a_guild_administrator_may_not_look(pool: TestClient) -> None:
    """Running their own server is not running Timothy."""
    for path in ("/ops/overview", "/ops/activity", "/ops/failures", "/ops/jobs"):
        assert pool.get(path, headers=headers(GUILD_ADMIN)).status_code == 403, path


def test_an_ordinary_member_may_not_look(pool: TestClient) -> None:
    assert pool.get("/ops/overview", headers=headers(MEMBER)).status_code == 403


# -- the overview ----------------------------------------------------------------------


def test_the_overview_reports_the_settings_the_numbers_depend_on(pool: TestClient) -> None:
    """`dry_run` decides what every other figure on the page means: zero bans reads as
    "nothing needed doing" with it off and "nothing was issued" with it on."""
    overview = _overview(pool)

    assert overview["dry_run"] is False
    assert overview["workers_enabled"] is False
    assert overview["enforcement_burst_limit"] == 25
    assert overview["management_guild_id"] == "100000000000000001"


def test_the_overview_says_when_dry_run_is_on(pool: TestClient, settings: Settings) -> None:
    """The single most important thing to be able to see during a cutover."""
    assert settings.dry_run is False
    assert _overview(pool)["dry_run"] is False


def test_the_overview_says_whether_login_is_configured(pool: TestClient) -> None:
    """A stack whose web UI nobody can log in to still comes up and still says 200 to
    every healthcheck. This is the only place that difference is visible."""
    assert _overview(pool)["login_configured"] is False


def test_the_overview_counts_what_timothy_holds(pool: TestClient) -> None:
    _list(pool)
    _subscribe(pool)

    counts = _overview(pool)["counts"]

    assert counts["guilds"] == 2
    assert counts["pools"] == 1
    assert counts["listings"] == 1
    # `global` does not exist in this fixture, so joining subscribed nobody to anything.
    assert counts["subscriptions"] == 1


def test_the_overview_counts_paused_guilds_separately(pool: TestClient) -> None:
    pool.patch(
        f"/guilds/{GUILD}", json={"enforcement_paused": True}, headers=headers(GUILD_ADMIN)
    )

    counts = _overview(pool)["counts"]

    assert counts["guilds"] == 2
    assert counts["guilds_paused"] == 1


# -- the queue -------------------------------------------------------------------------


def test_the_queue_depth_is_what_is_actually_waiting(pool: TestClient) -> None:
    _subscribe(pool)
    _list(pool)

    queue = _overview(pool)["queue"]

    assert queue["pending"] == 2
    assert queue["done"] == 0
    assert queue["oldest_pending_at"] is not None


def test_a_drained_queue_has_nothing_outstanding(
    pool: TestClient, enforcement: Enforcement
) -> None:
    _subscribe(pool)
    enforcement.drain()

    queue = _overview(pool)["queue"]

    assert queue["pending"] == 0
    assert queue["done"] == 1
    assert queue["oldest_pending_at"] is None


def test_sweep_progress_counts_only_the_guilds_still_to_sweep(
    pool: TestClient, enforcement: Enforcement
) -> None:
    """A round takes about two days against the migrated data. This counting down is what
    a working sweep looks like; it staying flat is what a wedged worker looks like.

    Sweeps are staggered evenly across the whole interval (phase 3), so a single `drain`
    clears the guilds that are due and leaves the rest — which is exactly the state this
    number exists to show.
    """
    assert enforcement.sweep() == 2
    assert _overview(pool)["queue"]["sweep_outstanding"] == 2

    enforcement.drain()
    assert _overview(pool)["queue"]["sweep_outstanding"] == 1

    past_the_whole_round = datetime.now(UTC) + timedelta(days=8)
    enforcement.drain(now=at(past_the_whole_round))
    assert _overview(pool)["queue"]["sweep_outstanding"] == 0


def test_the_jobs_view_shows_the_queue_newest_first(pool: TestClient) -> None:
    _subscribe(pool)
    _list(pool)

    jobs = pool.get("/ops/jobs", headers=headers(POOL_ADMIN)).json()

    assert [job["kind"] for job in jobs] == ["enforce_listing", "enforce_subscription"]
    assert jobs[0]["payload"]
    assert jobs[0]["attempts"] == 0


def test_the_jobs_view_can_be_narrowed_to_the_failures(
    pool: TestClient, settings: Settings, enforcement: Enforcement
) -> None:
    """A `failed` job is one that could not run at all — an unknown kind, a payload
    missing the key its handler needs. It is not a Discord call that did not work."""
    insert_job(settings, "nonsense", {})
    base = datetime.now(UTC)
    for attempt in range(1, settings.job_max_attempts + 2):
        enforcement.run_once(now=at(base + timedelta(hours=attempt)))

    failed = pool.get("/ops/jobs?status=failed", headers=headers(POOL_ADMIN)).json()

    assert [job["kind"] for job in failed] == ["nonsense"]
    assert failed[0]["last_error"]


def test_the_jobs_view_can_be_narrowed_to_one_kind(pool: TestClient) -> None:
    _subscribe(pool)
    _list(pool)

    jobs = pool.get(
        f"/ops/jobs?kind={JobKind.ENFORCE_LISTING.value}", headers=headers(POOL_ADMIN)
    ).json()

    assert [job["kind"] for job in jobs] == ["enforce_listing"]


def test_the_jobs_view_pages_by_id(pool: TestClient) -> None:
    _subscribe(pool)
    _list(pool)

    first = pool.get("/ops/jobs?limit=1", headers=headers(POOL_ADMIN)).json()
    second = pool.get(
        f"/ops/jobs?limit=1&before_id={first[0]['id']}", headers=headers(POOL_ADMIN)
    ).json()

    assert second[0]["id"] < first[0]["id"]


# -- activity --------------------------------------------------------------------------


def test_activity_counts_what_people_did(pool: TestClient) -> None:
    _list(pool)

    assert _activity(pool)["listing.create"] == 1


def test_activity_counts_timothys_own_actions_too(
    pool: TestClient, enforcement: Enforcement, discord: FakeDiscord
) -> None:
    """These are the ones nobody typed, and so the ones most worth being able to see."""
    _subscribe(pool)
    discord.add_member(GUILD, LISTED_USER)
    _list(pool)
    enforcement.drain()

    assert _activity(pool)["enforcement.ban"] == 1


def test_a_dry_run_says_what_it_would_have_done(
    pool: TestClient, settings: Settings, discord: FakeDiscord
) -> None:
    """During a cutover the whole question is "how many bans would that have been", and
    a bare `enforcement.dry_run` count cannot answer it — a warn and a ban are the same
    action with different consequences."""
    discord.add_member(GUILD, LISTED_USER)
    # Listed before anybody subscribes, so this job reaches no guild and leaves the queue
    # empty — the dry run below then has exactly one intention to report.
    _list(pool)
    worker(pool, settings, discord, dry_run=False).drain()
    _subscribe(pool)

    worker(pool, settings, discord, dry_run=True).drain()

    activity = _activity(pool)
    assert activity["enforcement.dry_run:ban"] == 1
    assert "enforcement.ban" not in activity


def test_a_dry_run_warn_is_counted_apart_from_a_dry_run_ban(
    pool: TestClient, settings: Settings, discord: FakeDiscord
) -> None:
    pool.put(
        f"/guilds/{GUILD}/notification-channel",
        json={"channel_id": str(CHANNEL)},
        headers=headers(GUILD_ADMIN),
    )
    discord.add_member(GUILD, LISTED_USER)
    _list(pool)
    worker(pool, settings, discord, dry_run=False).drain()
    _subscribe(pool, level="warn")

    worker(pool, settings, discord, dry_run=True).drain()

    activity = _activity(pool)
    assert activity["enforcement.dry_run:warn"] == 1
    assert "enforcement.dry_run:ban" not in activity


def test_activity_is_bounded_by_the_window_it_was_asked_for(pool: TestClient) -> None:
    _list(pool)

    assert _activity(pool, "?days=1")["listing.create"] == 1
    assert pool.get("/ops/activity?days=0", headers=headers(POOL_ADMIN)).status_code == 422
    assert pool.get("/ops/activity?days=91", headers=headers(POOL_ADMIN)).status_code == 422


def test_a_day_with_nothing_in_it_is_absent_rather_than_zero(pool: TestClient) -> None:
    """Reporting zeroes for days it never observed would be the API inventing rows in an
    append-only record. A chart fills its own gaps."""
    _list(pool)

    days = {
        point["day"] for point in pool.get("/ops/activity?days=90", headers=headers()).json()
    }

    assert len(days) == 1


# -- failures --------------------------------------------------------------------------


def test_failures_are_grouped_by_guild_and_cause(
    pool: TestClient, enforcement: Enforcement, discord: FakeDiscord
) -> None:
    """The everyday shape is one server, one sentence, repeated for everyone listed —
    a guild that granted Timothy no ban permission. One line, not four hundred."""
    _subscribe(pool)
    for user_id in (LISTED_USER, LISTED_USER + 1):
        discord.add_member(GUILD, user_id)
        discord.fail(
            "ban",
            guild_id=GUILD,
            user_id=user_id,
            error=ForbiddenError("Timothy cannot ban this member"),
        )
        _list(pool, user_id)
    enforcement.drain()

    failures = pool.get("/ops/failures", headers=headers(POOL_ADMIN)).json()

    assert len(failures) == 1
    assert failures[0]["guild_id"] == str(GUILD)
    assert failures[0]["count"] == 2
    assert "cannot ban" in failures[0]["reason"]


def test_nothing_failing_is_an_empty_list(pool: TestClient) -> None:
    assert pool.get("/ops/failures", headers=headers(POOL_ADMIN)).json() == []
