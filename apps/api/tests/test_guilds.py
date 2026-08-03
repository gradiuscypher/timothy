"""Registration, the auto-subscription that follows it, and the per-guild pause."""

from collections.abc import Callable

from fastapi.testclient import TestClient

from timothy_api.app import create_app
from timothy_api.jobs import JobKind
from timothy_api.settings import Settings
from timothy_core.ports.fake import FakeDiscord

from .conftest import (
    GUILD,
    GUILD_ADMIN,
    MANAGEMENT_ADMIN,
    MANAGEMENT_GUILD,
    MEMBER,
    OUTSIDER,
    POOL_MANAGER,
    FakeOAuth,
    headers,
    sign_in,
)

Enqueued = Callable[[], list[tuple[str, dict[str, int]]]]


def test_only_timothy_registers_a_guild(client: TestClient) -> None:
    """There is no human in the loop when the bot joins, and so no Discord permission to
    derive authority from."""
    response = client.put(f"/guilds/{GUILD}", headers=headers(POOL_MANAGER))

    assert response.status_code == 403


def test_registration_is_idempotent(client: TestClient) -> None:
    """The bot re-announces its guilds on every gateway reconnect."""
    first = client.put(f"/guilds/{GUILD}", headers=headers("system"))
    second = client.put(f"/guilds/{GUILD}", headers=headers("system"))

    assert first.status_code == 200
    assert second.json() == first.json()


def test_registration_records_what_the_guild_is_called(client: TestClient) -> None:
    """The gateway has the name for free; asking Discord for it later would cost a call
    per guild, out of the same budget enforcement runs on."""
    response = client.put(
        f"/guilds/{GUILD}", json={"name": "Neon Atrium"}, headers=headers("system")
    )

    assert response.json()["name"] == "Neon Atrium"


def test_registering_without_a_name_leaves_the_guild_unnamed(client: TestClient) -> None:
    """A bot older than this field sends no body at all, and must still be able to
    register."""
    response = client.put(f"/guilds/{GUILD}", headers=headers("system"))

    assert response.json()["name"] is None


def test_re_registering_refreshes_the_name(client: TestClient) -> None:
    """How a rename lands, and how a guild registered before names were stored gets
    one: the announcement on every reconnect carries the current name."""
    client.put(f"/guilds/{GUILD}", headers=headers("system"))

    response = client.put(
        f"/guilds/{GUILD}", json={"name": "Neon Atrium"}, headers=headers("system")
    )

    assert response.json()["name"] == "Neon Atrium"
    assert (
        client.get(f"/guilds/{GUILD}", headers=headers(GUILD_ADMIN)).json()["name"]
        == "Neon Atrium"
    )


def test_a_registration_with_no_name_does_not_clear_the_stored_one(
    client: TestClient,
) -> None:
    """A caller with no name to offer is not asserting that the guild has none."""
    client.put(f"/guilds/{GUILD}", json={"name": "Neon Atrium"}, headers=headers("system"))

    client.put(f"/guilds/{GUILD}", headers=headers("system"))

    assert (
        client.get(f"/guilds/{GUILD}", headers=headers(GUILD_ADMIN)).json()["name"]
        == "Neon Atrium"
    )


def test_joining_subscribes_the_guild_to_the_shared_pool(client: TestClient) -> None:
    """ADR 0002 keeps what the old bot did — every guild enforced `global` — while
    dropping the reserved name that made it impossible to leave."""
    client.post("/pools", json={"name": "global"}, headers=headers(POOL_MANAGER))

    client.put(f"/guilds/{GUILD}", headers=headers("system"))

    subscriptions = client.get(
        f"/guilds/{GUILD}/subscriptions", headers=headers(GUILD_ADMIN)
    ).json()
    assert [(entry["pool_name"], entry["level"]) for entry in subscriptions] == [
        ("global", "ban")
    ]


def test_the_shared_subscription_is_attributed_to_timothy(client: TestClient) -> None:
    """Not to the magic user ID `"0"` the old bot used, which was indistinguishable from
    a real person (ADR 0006)."""
    client.post("/pools", json={"name": "global"}, headers=headers(POOL_MANAGER))
    client.put(f"/guilds/{GUILD}", headers=headers("system"))

    subscriptions = client.get(
        f"/guilds/{GUILD}/subscriptions", headers=headers(GUILD_ADMIN)
    ).json()

    assert subscriptions[0]["created_by"] == "system"


def test_the_shared_subscription_can_be_declined(client: TestClient) -> None:
    """The whole point of ADR 0002: it is an ordinary row, and the guild owns it."""
    client.post("/pools", json={"name": "global"}, headers=headers(POOL_MANAGER))
    client.put(f"/guilds/{GUILD}", headers=headers("system"))

    dropped = client.delete(
        f"/guilds/{GUILD}/subscriptions/global", headers=headers(GUILD_ADMIN)
    )

    assert dropped.status_code == 204
    assert (
        client.get(f"/guilds/{GUILD}/subscriptions", headers=headers(GUILD_ADMIN)).json() == []
    )


def test_a_reconnect_does_not_undo_that_decision(client: TestClient) -> None:
    """Only the first registration auto-subscribes. Re-subscribing every time the
    gateway blinked would make the opt-out meaningless."""
    client.post("/pools", json={"name": "global"}, headers=headers(POOL_MANAGER))
    client.put(f"/guilds/{GUILD}", headers=headers("system"))
    client.delete(f"/guilds/{GUILD}/subscriptions/global", headers=headers(GUILD_ADMIN))

    client.put(f"/guilds/{GUILD}", headers=headers("system"))

    assert (
        client.get(f"/guilds/{GUILD}/subscriptions", headers=headers(GUILD_ADMIN)).json() == []
    )


def test_joining_with_no_shared_pool_yet_subscribes_to_nothing(
    client: TestClient, enqueued: Enqueued
) -> None:
    client.put(f"/guilds/{GUILD}", headers=headers("system"))

    assert (
        client.get(f"/guilds/{GUILD}/subscriptions", headers=headers(GUILD_ADMIN)).json() == []
    )
    assert enqueued() == []


def test_the_auto_subscription_enqueues_enforcement(
    client: TestClient, enqueued: Enqueued
) -> None:
    pool = client.post("/pools", json={"name": "global"}, headers=headers(POOL_MANAGER)).json()

    client.put(f"/guilds/{GUILD}", headers=headers("system"))

    assert enqueued() == [
        (
            JobKind.ENFORCE_SUBSCRIPTION.value,
            {"guild_id": GUILD, "pool_id": pool["id"]},
        )
    ]


def test_auto_subscription_can_be_switched_off_entirely(
    settings: Settings, discord: FakeDiscord
) -> None:
    """An empty `auto_subscribe_pool` disables the behaviour, for a deployment with no
    shared banlist at all."""
    without = settings.model_copy(update={"auto_subscribe_pool": ""})

    with TestClient(create_app(without, discord_port=discord)) as client:
        client.post("/pools", json={"name": "global"}, headers=headers(POOL_MANAGER))
        client.put(f"/guilds/{GUILD}", headers=headers("system"))

        assert (
            client.get(f"/guilds/{GUILD}/subscriptions", headers=headers(GUILD_ADMIN)).json()
            == []
        )


def test_a_guild_administrator_reads_their_own_guild(registered: TestClient) -> None:
    response = registered.get(f"/guilds/{GUILD}", headers=headers(GUILD_ADMIN))

    assert response.status_code == 200
    assert response.json()["enforcement_paused"] is False


def test_an_ordinary_member_may_not(registered: TestClient) -> None:
    assert registered.get(f"/guilds/{GUILD}", headers=headers(MEMBER)).status_code == 403


def test_authority_over_a_guild_timothy_is_not_in_is_no_authority(
    registered: TestClient,
) -> None:
    response = registered.get(f"/guilds/{GUILD + 99}", headers=headers(GUILD_ADMIN))

    assert response.status_code == 403


def test_enforcement_can_be_paused_and_resumed(
    registered: TestClient, enqueued: Enqueued
) -> None:
    """ADR 0007's per-guild rail: isolate one guild without stopping the service."""
    paused = registered.patch(
        f"/guilds/{GUILD}", json={"enforcement_paused": True}, headers=headers(GUILD_ADMIN)
    )

    assert paused.status_code == 200
    assert paused.json()["enforcement_paused"] is True
    assert enqueued() == []

    resumed = registered.patch(
        f"/guilds/{GUILD}", json={"enforcement_paused": False}, headers=headers(GUILD_ADMIN)
    )

    assert resumed.json()["enforcement_paused"] is False
    assert enqueued() == [(JobKind.ENFORCE_GUILD.value, {"guild_id": GUILD})]


def test_pausing_twice_enqueues_nothing(registered: TestClient, enqueued: Enqueued) -> None:
    """Resuming is what needs a catch-up, because everything that happened while paused
    deliberately recorded nothing."""
    for _ in range(2):
        registered.patch(
            f"/guilds/{GUILD}",
            json={"enforcement_paused": True},
            headers=headers(GUILD_ADMIN),
        )

    assert enqueued() == []


def test_leaving_a_guild_forgets_its_configuration(registered: TestClient) -> None:
    registered.put(
        f"/guilds/{GUILD}/notification-channel",
        json={"channel_id": "400000000000000001"},
        headers=headers(GUILD_ADMIN),
    )

    assert registered.delete(f"/guilds/{GUILD}", headers=headers("system")).status_code == 204
    assert registered.get(f"/guilds/{GUILD}", headers=headers(GUILD_ADMIN)).status_code == 404


def test_deregistering_a_guild_that_is_not_there_is_a_404(client: TestClient) -> None:
    assert client.delete(f"/guilds/{GUILD}", headers=headers("system")).status_code == 404


# -- the web UI's front door -----------------------------------------------------------


def test_listing_guilds_returns_only_the_ones_the_caller_administers(
    registered: TestClient,
) -> None:
    """Filtering rather than gating. There is no operation "administrator somewhere", so
    the list is built by asking Discord about each candidate."""
    listed = registered.get("/guilds", headers=headers(GUILD_ADMIN)).json()

    assert [entry["guild_id"] for entry in listed] == [str(GUILD)]


def test_a_pool_manager_administers_no_guild_and_so_lists_none(
    registered: TestClient,
) -> None:
    """Owning pools is authority over pools, not over the guilds that subscribe to them.
    Their configuration stays their own business.

    Since ADR 0012 the pool manager role carries no Discord permissions at all, so the
    list is empty rather than just short — which is the sharper version of the same
    point. The guild that does show up here is the one you administer, below."""
    listed = registered.get("/guilds", headers=headers(POOL_MANAGER)).json()

    assert listed == []


def test_a_management_administrator_lists_the_management_guild(
    registered: TestClient,
) -> None:
    """The other side of the split: they administer that guild, so it is theirs to
    configure — and pools are still not (ADR 0012)."""
    listed = registered.get("/guilds", headers=headers(MANAGEMENT_ADMIN)).json()

    assert [entry["guild_id"] for entry in listed] == [str(MANAGEMENT_GUILD)]


def test_a_member_who_administers_nothing_gets_an_empty_list(
    registered: TestClient,
) -> None:
    assert registered.get("/guilds", headers=headers(MEMBER)).json() == []


def test_listing_guilds_is_refused_to_someone_outside_every_guild(
    registered: TestClient,
) -> None:
    assert registered.get("/guilds", headers=headers(OUTSIDER)).status_code == 403


def test_a_browser_is_only_asked_about_the_guilds_discord_named(
    registered: TestClient, oauth: FakeOAuth, discord: FakeDiscord
) -> None:
    """The candidates are the login snapshot intersected with Timothy's guilds. Without
    that, a person in one server would cost a resolved permission for all 123.

    Every snapshot names the management guild — that is the price of a session at all
    (ADR 0013) — so this one is narrowed down to exactly that. The guild this person
    actually administers is not in the snapshot and is never asked about, which is the
    same edge ADR 0010 records: a guild Timothy joins after somebody logs in is invisible
    to them until they log in again.
    """
    sign_in(registered, oauth, user_id=GUILD_ADMIN, guild_ids=(MANAGEMENT_GUILD,))
    discord.calls.clear()

    listed = registered.get("/guilds").json()

    assert listed == []
    permissions_asked = [call.guild_id for call in discord.calls_of("guild_permissions")]
    assert permissions_asked == [MANAGEMENT_GUILD]
