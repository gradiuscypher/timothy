"""The channel where Timothy reports what it did in a guild."""

from fastapi.testclient import TestClient

from timothy_core.ports.fake import FakeDiscord

from .conftest import CHANNEL, GUILD, GUILD_ADMIN, MEMBER, headers


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
        json={"channel_id": str(CHANNEL + 1)},
        headers=headers(GUILD_ADMIN),
    )

    assert moved.json()["channel_id"] == str(CHANNEL + 1)


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
    refusal as an enforcement outcome, which is the durable answer."""
    response = registered.put(
        f"/guilds/{GUILD}/notification-channel",
        json={"channel_id": "999000000000000001"},
        headers=headers(GUILD_ADMIN),
    )

    assert response.status_code == 200
    assert discord.calls_of("post_message") == []
