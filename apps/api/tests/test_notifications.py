"""The channel where Timothy reports what it did in a guild."""

from fastapi.testclient import TestClient

from timothy_core.ports.discord import ForbiddenError
from timothy_core.ports.fake import FakeDiscord

from .conftest import (
    CATEGORY,
    CHANNEL,
    GUILD,
    GUILD_ADMIN,
    MEMBER,
    OTHER_CHANNEL,
    OTHER_GUILD,
    SECOND_CHANNEL,
    headers,
)


def test_a_guild_administrator_sets_the_channel(registered: TestClient) -> None:
    response = registered.put(
        f"/guilds/{GUILD}/notification-channel",
        json={"channel_id": str(CHANNEL)},
        headers=headers(GUILD_ADMIN),
    )

    assert response.status_code == 200
    assert response.json()["channel_id"] == str(CHANNEL)


def test_a_member_may_not(registered: TestClient) -> None:
    response = registered.put(
        f"/guilds/{GUILD}/notification-channel",
        json={"channel_id": str(CHANNEL)},
        headers=headers(MEMBER),
    )

    assert response.status_code == 403


def test_setting_it_again_moves_it(registered: TestClient) -> None:
    """One channel per guild, so this is a `PUT` and not a create."""
    registered.put(
        f"/guilds/{GUILD}/notification-channel",
        json={"channel_id": str(CHANNEL)},
        headers=headers(GUILD_ADMIN),
    )

    moved = registered.put(
        f"/guilds/{GUILD}/notification-channel",
        json={"channel_id": str(SECOND_CHANNEL)},
        headers=headers(GUILD_ADMIN),
    )

    assert moved.json()["channel_id"] == str(SECOND_CHANNEL)


def test_reading_it_before_it_is_set_is_a_404(registered: TestClient) -> None:
    response = registered.get(
        f"/guilds/{GUILD}/notification-channel", headers=headers(GUILD_ADMIN)
    )

    assert response.status_code == 404


def test_it_can_be_read_back(registered: TestClient) -> None:
    registered.put(
        f"/guilds/{GUILD}/notification-channel",
        json={"channel_id": str(CHANNEL)},
        headers=headers(GUILD_ADMIN),
    )

    response = registered.get(
        f"/guilds/{GUILD}/notification-channel", headers=headers(GUILD_ADMIN)
    )

    assert response.status_code == 200
    assert response.json()["guild_id"] == str(GUILD)


def test_it_can_be_removed(registered: TestClient) -> None:
    registered.put(
        f"/guilds/{GUILD}/notification-channel",
        json={"channel_id": str(CHANNEL)},
        headers=headers(GUILD_ADMIN),
    )

    removed = registered.delete(
        f"/guilds/{GUILD}/notification-channel", headers=headers(GUILD_ADMIN)
    )

    assert removed.status_code == 204
    assert (
        registered.get(
            f"/guilds/{GUILD}/notification-channel", headers=headers(GUILD_ADMIN)
        ).status_code
        == 404
    )


def test_removing_one_that_is_not_there_is_a_404(registered: TestClient) -> None:
    response = registered.delete(
        f"/guilds/{GUILD}/notification-channel", headers=headers(GUILD_ADMIN)
    )

    assert response.status_code == 404


def test_timothy_does_not_check_it_can_post(
    registered: TestClient, discord: FakeDiscord
) -> None:
    """It has no way to know today whether it still can tomorrow. Phase 3 records the
    refusal as an enforcement outcome, which is the durable answer.

    The channel below belongs to the guild and cannot be posted to. Ownership is settled
    here; permission is not, and the two are separate on purpose.
    """
    discord.fail_message(CHANNEL, ForbiddenError("no"))

    response = registered.put(
        f"/guilds/{GUILD}/notification-channel",
        json={"channel_id": str(CHANNEL)},
        headers=headers(GUILD_ADMIN),
    )

    assert response.status_code == 200
    assert discord.calls_of("post_message") == []


def test_a_guild_may_not_nominate_another_guilds_channel(
    registered: TestClient, discord: FakeDiscord
) -> None:
    """The whole reason ownership is checked at all.

    Without this, an administrator of one member server could have Timothy carry its warn
    notices — which name users present in it — into a server it does not run.
    """
    response = registered.put(
        f"/guilds/{GUILD}/notification-channel",
        json={"channel_id": str(OTHER_CHANNEL)},
        headers=headers(GUILD_ADMIN),
    )

    assert response.status_code == 422
    assert str(OTHER_CHANNEL) in response.json()["detail"]
    # And nothing was stored, so enforcement has nowhere to carry anything.
    assert (
        registered.get(
            f"/guilds/{GUILD}/notification-channel", headers=headers(GUILD_ADMIN)
        ).status_code
        == 404
    )


def test_the_refusal_does_not_say_which_guild_owns_it(registered: TestClient) -> None:
    """The caller proved they can name a channel, not that they may locate it."""
    response = registered.put(
        f"/guilds/{GUILD}/notification-channel",
        json={"channel_id": str(OTHER_CHANNEL)},
        headers=headers(GUILD_ADMIN),
    )

    assert str(OTHER_GUILD) not in response.json()["detail"]


def test_a_channel_nothing_can_be_posted_to_is_refused(registered: TestClient) -> None:
    """A category is a container, not a place to say something."""
    response = registered.put(
        f"/guilds/{GUILD}/notification-channel",
        json={"channel_id": str(CATEGORY)},
        headers=headers(GUILD_ADMIN),
    )

    assert response.status_code == 422


def test_a_channel_timothy_cannot_see_is_a_404(registered: TestClient) -> None:
    response = registered.put(
        f"/guilds/{GUILD}/notification-channel",
        json={"channel_id": "999000000000000001"},
        headers=headers(GUILD_ADMIN),
    )

    assert response.status_code == 404


def test_ownership_is_settled_once_and_not_at_send_time(
    registered: TestClient, discord: FakeDiscord
) -> None:
    """A channel ID is bound to its guild for life, so the send path spends no call on it.

    This is what keeps the check off the sweep, which already costs one Discord lookup
    per listed user per guild.
    """
    registered.put(
        f"/guilds/{GUILD}/notification-channel",
        json={"channel_id": str(CHANNEL)},
        headers=headers(GUILD_ADMIN),
    )

    assert len(discord.calls_of("fetch_channel")) == 1
