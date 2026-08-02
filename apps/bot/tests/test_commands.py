"""What each slash command asks the backend for, and what it says back.

The handlers are ordinary coroutines, so these call them. What is being pinned is the
pair every command is: the request that goes out — path, body, and the moderator it acts
for — and the embed a moderator reads, whose wording is inherited from a bot people have
been using for years.
"""

import httpx
import pytest
from discord import app_commands
from support import (
    CHANNEL,
    GUILD,
    LISTED_USER,
    MODERATOR,
    Backend,
    FakeInteraction,
    field,
    invoke,
    is_green,
    is_red,
)

from timothy_bot.api import SYSTEM, Api
from timothy_bot.commands import exceptions, listings, notifications, pools, subscriptions

pytestmark = pytest.mark.anyio


# -- pools -----------------------------------------------------------------------------


async def test_add_pool_creates_one(backend: Backend, interaction: FakeInteraction) -> None:
    backend.replies(201, {"id": 1, "name": "spam"})

    embed = await invoke(pools.add_pool, interaction, pool_name="spam", pool_desc="spammers")

    assert backend.called == ("POST", "/pools")
    assert backend.sent == {"name": "spam", "description": "spammers"}
    assert backend.request.headers["X-Timothy-Actor"] == f"user:{MODERATOR}"
    assert embed.description == "Banpool `spam` was created successfully"
    assert is_green(embed)


async def test_add_pool_reports_why_it_could_not(
    backend: Backend, interaction: FakeInteraction
) -> None:
    backend.fails(409, "pool already exists: spam")

    embed = await invoke(pools.add_pool, interaction, pool_name="spam", pool_desc="spammers")

    assert embed.description == "Banpool `spam` failed to create.\n\npool already exists: spam"
    assert is_red(embed)


async def test_delete_pool_deletes_one(backend: Backend, interaction: FakeInteraction) -> None:
    backend.replies(204)

    embed = await invoke(pools.delete_pool, interaction, pool_name="spam")

    assert backend.called == ("DELETE", "/pools/spam")
    assert embed.description == "Banpool `spam` was deleted successfully"


async def test_deleting_a_pool_never_asks_for_a_revert(
    backend: Backend, interaction: FakeInteraction
) -> None:
    """No `?revert=true`. Tidying a pool away and lifting every ban it caused are not the
    same keystroke (ADR 0005)."""
    backend.replies(204)

    await invoke(pools.delete_pool, interaction, pool_name="spam")

    assert backend.request.url.params.get("revert") is None


async def test_delete_pool_reports_why_it_could_not(
    backend: Backend, interaction: FakeInteraction
) -> None:
    backend.fails(404, "no such pool: spma")

    embed = await invoke(pools.delete_pool, interaction, pool_name="spma")

    assert embed.description == "Banpool `spma` failed to delete.\n\nno such pool: spma"
    assert is_red(embed)


async def test_list_pools_gives_one_field_per_pool(
    backend: Backend, interaction: FakeInteraction
) -> None:
    backend.replies(
        200,
        [
            {"name": "spam", "description": "spammers"},
            {"name": "global", "description": "the shared list"},
        ],
    )

    embed = await invoke(pools.list_pools, interaction)

    assert backend.called == ("GET", "/pools")
    assert field(embed, "spam") == "spammers"
    assert field(embed, "global") == "the shared list"


async def test_a_pool_without_a_description_still_renders(
    backend: Backend, interaction: FakeInteraction
) -> None:
    """Discord rejects an embed field with an empty value, and descriptions are optional
    now where Mongo's were not."""
    backend.replies(200, [{"name": "spam", "description": None}])

    embed = await invoke(pools.list_pools, interaction)

    assert field(embed, "spam") == pools.NO_DESCRIPTION


async def test_list_pools_says_why_it_failed(
    backend: Backend, interaction: FakeInteraction
) -> None:
    """The one command a member with no administrator anywhere can reach, so the reason
    is the answer they most need — the old bot dropped it."""
    backend.fails(403, "not permitted: read_pools")

    embed = await invoke(pools.list_pools, interaction)

    assert embed.description == "Failed to list Banpools\n\nnot permitted: read_pools"
    assert is_red(embed)


# -- listings --------------------------------------------------------------------------


async def test_add_ban_creates_a_listing(
    backend: Backend, interaction: FakeInteraction
) -> None:
    """`/add_ban` creates a Listing. It bans nobody by itself — CONTEXT.md."""
    backend.replies(201, {"id": 1})

    embed = await invoke(
        listings.add_ban,
        interaction,
        user_id=str(LISTED_USER),
        pool_name="spam",
        reason="raiding",
    )

    assert backend.called == ("POST", "/pools/spam/listings")
    assert backend.sent == {"user_id": str(LISTED_USER), "reason": "raiding"}
    assert embed.description == f"`{LISTED_USER}` was added to `spam` successfully"
    assert field(embed, "Banpool Name") == "spam"
    assert field(embed, "Ban Reason") == "raiding"


async def test_add_ban_mentions_the_user_rather_than_fetching_them(
    backend: Backend, interaction: FakeInteraction
) -> None:
    """The bot makes no Discord calls of its own, and the discriminator the old embed
    showed no longer exists."""
    backend.replies(201, {"id": 1})

    embed = await invoke(
        listings.add_ban,
        interaction,
        user_id=str(LISTED_USER),
        pool_name="spam",
        reason="raiding",
    )

    assert field(embed, "User") == f"<@{LISTED_USER}>"


async def test_add_ban_rejects_something_that_is_not_a_user_id(
    backend: Backend, interaction: FakeInteraction
) -> None:
    """The old bot parsed this with `unwrap` and panicked."""
    embed = await invoke(
        listings.add_ban, interaction, user_id="gradius", pool_name="spam", reason="raiding"
    )

    assert backend.requests == []
    assert embed.description == "Failed to add `gradius` to spam.\n\nthat is not a user ID"
    assert is_red(embed)


async def test_add_ban_reports_why_it_could_not(
    backend: Backend, interaction: FakeInteraction
) -> None:
    backend.fails(409, f"already listed in spam: {LISTED_USER}")

    embed = await invoke(
        listings.add_ban,
        interaction,
        user_id=str(LISTED_USER),
        pool_name="spam",
        reason="raiding",
    )

    assert embed.description == (
        f"Failed to add `{LISTED_USER}` to spam.\n\nalready listed in spam: {LISTED_USER}"
    )


async def test_delete_ban_removes_the_listing(
    backend: Backend, interaction: FakeInteraction
) -> None:
    backend.replies(204)

    embed = await invoke(
        listings.delete_ban, interaction, user_id=str(LISTED_USER), pool_name="spam"
    )

    assert backend.called == ("DELETE", f"/pools/spam/listings/{LISTED_USER}")
    assert embed.description == f"`{LISTED_USER}` was removed from `spam` successfully"


async def test_delete_ban_never_asks_for_a_revert(
    backend: Backend, interaction: FakeInteraction
) -> None:
    """The bans it caused stay, which is what the old bot did by having no other option
    and what the API does by default."""
    backend.replies(204)

    await invoke(listings.delete_ban, interaction, user_id=str(LISTED_USER), pool_name="spam")

    assert backend.request.url.params.get("revert") is None


async def test_delete_ban_reports_why_it_could_not(
    backend: Backend, interaction: FakeInteraction
) -> None:
    backend.fails(404, "no such listing")

    embed = await invoke(
        listings.delete_ban, interaction, user_id=str(LISTED_USER), pool_name="spam"
    )

    assert embed.description == (
        f"Failed to remove `{LISTED_USER}` from spam.\n\nno such listing"
    )


async def test_get_user_bans_lists_the_pools(
    backend: Backend, interaction: FakeInteraction
) -> None:
    backend.replies(200, [{"pool_name": "global"}, {"pool_name": "spam"}])

    embed = await invoke(listings.get_user_bans, interaction, user_id=str(LISTED_USER))

    assert backend.called == ("GET", f"/users/{LISTED_USER}/listings")
    assert embed.description == "global\nspam"


async def test_get_user_bans_on_someone_listed_nowhere(
    backend: Backend, interaction: FakeInteraction
) -> None:
    """Not an error, and not an empty description either: Discord rejects those."""
    backend.replies(200, [])

    embed = await invoke(listings.get_user_bans, interaction, user_id=str(LISTED_USER))

    assert embed.description is None
    assert is_green(embed)


async def test_get_user_bans_reports_a_failure(
    backend: Backend, interaction: FakeInteraction
) -> None:
    backend.fails(403, "not permitted: read_pools")

    embed = await invoke(listings.get_user_bans, interaction, user_id=str(LISTED_USER))

    assert embed.description == "Unable to fetch bans for that user"
    assert is_red(embed)


# -- subscriptions ---------------------------------------------------------------------


def level(value: str) -> app_commands.Choice[str]:
    return app_commands.Choice(name=value, value=value)


async def test_add_subscription_subscribes_the_guild(
    backend: Backend, interaction: FakeInteraction
) -> None:
    embed = await invoke(
        subscriptions.add_subscription,
        interaction,
        pool_name="spam",
        subscription_level=level("ban"),
    )

    assert backend.called == ("PUT", f"/guilds/{GUILD}/subscriptions/spam")
    assert backend.sent == {"level": "ban"}
    assert embed.description == "Subscription for spam:ban has been created successfully"


async def test_add_subscription_reports_why_it_could_not(
    backend: Backend, interaction: FakeInteraction
) -> None:
    backend.fails(404, "no such pool: spma")

    embed = await invoke(
        subscriptions.add_subscription,
        interaction,
        pool_name="spma",
        subscription_level=level("warn"),
    )

    assert embed.description == "Failed to subscribe to spma\n\nno such pool: spma"


async def test_delete_subscription_says_the_bans_stay(
    backend: Backend, interaction: FakeInteraction
) -> None:
    """Still true: no slash command asks the API for a revert."""
    backend.replies(204)

    embed = await invoke(subscriptions.delete_subscription, interaction, pool_name="spam")

    assert backend.called == ("DELETE", f"/guilds/{GUILD}/subscriptions/spam")
    assert backend.request.url.params.get("revert") is None
    assert embed.description == (
        "Subscription for spam has been deleted successfully. Please note, this does not "
        "remove the bans already in place."
    )


async def test_delete_subscription_reports_why_it_could_not(
    backend: Backend, interaction: FakeInteraction
) -> None:
    backend.fails(404, "no such subscription")

    embed = await invoke(subscriptions.delete_subscription, interaction, pool_name="spam")

    assert embed.description == "Subscription to spam failed to delete:\n\nno such subscription"


async def test_list_subscriptions_reflects_stored_state(
    backend: Backend, interaction: FakeInteraction
) -> None:
    """No fabricated `global:ban` line. `global` is an ordinary pool now, and a guild
    that unsubscribed from it has to be able to see that it did (ADR 0002)."""
    backend.replies(200, [{"pool_name": "spam", "level": "warn"}])

    embed = await invoke(subscriptions.list_subscriptions, interaction)

    assert backend.called == ("GET", f"/guilds/{GUILD}/subscriptions")
    assert embed.description == "spam:warn"


async def test_list_subscriptions_reports_a_failure(
    backend: Backend, interaction: FakeInteraction
) -> None:
    backend.fails(403, "not permitted: manage_subscriptions")

    embed = await invoke(subscriptions.list_subscriptions, interaction)

    assert embed.description == (
        "Unable to fetch subscriptions.\n\nnot permitted: manage_subscriptions"
    )


# -- exceptions ------------------------------------------------------------------------


async def test_add_exception_vouches_for_a_user(
    backend: Backend, interaction: FakeInteraction
) -> None:
    backend.replies(201, {"user_id": str(LISTED_USER)})

    embed = await invoke(exceptions.add_exception, interaction, user_id=str(LISTED_USER))

    assert backend.called == ("PUT", f"/guilds/{GUILD}/exceptions/{LISTED_USER}")
    assert embed.description == f"Exception for {LISTED_USER} has been created successfully"


async def test_add_exception_does_not_lift_an_existing_ban(
    backend: Backend, interaction: FakeInteraction
) -> None:
    """An exception is read as "from now on". Lifting a ban already issued is a revert,
    and every revert is asked for explicitly."""
    backend.replies(201, {})

    await invoke(exceptions.add_exception, interaction, user_id=str(LISTED_USER))

    assert backend.request.url.params.get("revert") is None


async def test_add_exception_rejects_something_that_is_not_a_user_id(
    backend: Backend, interaction: FakeInteraction
) -> None:
    embed = await invoke(exceptions.add_exception, interaction, user_id="@gradius")

    assert backend.requests == []
    assert embed.description == (
        "Exception for @gradius failed to create:\n\nthat is not a user ID"
    )


async def test_add_exception_reports_why_it_could_not(
    backend: Backend, interaction: FakeInteraction
) -> None:
    backend.fails(409, "already excepted")

    embed = await invoke(exceptions.add_exception, interaction, user_id=str(LISTED_USER))

    assert embed.description == (
        f"Exception for {LISTED_USER} failed to create:\n\nalready excepted"
    )


async def test_delete_exception_withdraws_the_vouch(
    backend: Backend, interaction: FakeInteraction
) -> None:
    backend.replies(204)

    embed = await invoke(exceptions.delete_exception, interaction, user_id=str(LISTED_USER))

    assert backend.called == ("DELETE", f"/guilds/{GUILD}/exceptions/{LISTED_USER}")
    assert embed.description == f"Exception for {LISTED_USER} has been deleted successfully"


async def test_delete_exception_rejects_something_that_is_not_a_user_id(
    backend: Backend, interaction: FakeInteraction
) -> None:
    embed = await invoke(exceptions.delete_exception, interaction, user_id="")

    assert backend.requests == []
    assert embed.description == "Exception for  failed to delete:\n\nthat is not a user ID"


async def test_delete_exception_reports_why_it_could_not(
    backend: Backend, interaction: FakeInteraction
) -> None:
    backend.fails(404, "no such exception")

    embed = await invoke(exceptions.delete_exception, interaction, user_id=str(LISTED_USER))

    assert embed.description == (
        f"Exception for {LISTED_USER} failed to delete:\n\nno such exception"
    )


async def test_list_exceptions_lists_the_user_ids(
    backend: Backend, interaction: FakeInteraction
) -> None:
    backend.replies(200, [{"user_id": str(LISTED_USER)}])

    embed = await invoke(exceptions.list_exceptions, interaction)

    assert backend.called == ("GET", f"/guilds/{GUILD}/exceptions")
    assert embed.description == str(LISTED_USER)


async def test_list_exceptions_reports_a_failure(
    backend: Backend, interaction: FakeInteraction
) -> None:
    backend.fails(404, "no such guild")

    embed = await invoke(exceptions.list_exceptions, interaction)

    assert embed.description == "Unable to fetch exceptions.\n\nno such guild"


# -- notification channel --------------------------------------------------------------


class FakeChannel:
    """What Discord hands a `TextChannel` option, reduced to its ID."""

    def __init__(self, channel_id: int) -> None:
        self.id = channel_id


async def test_add_notification_points_the_guild_at_a_channel(
    backend: Backend, interaction: FakeInteraction
) -> None:
    embed = await invoke(
        notifications.add_notification,
        interaction,
        channel_id=FakeChannel(CHANNEL),
    )

    assert backend.called == ("PUT", f"/guilds/{GUILD}/notification-channel")
    assert backend.sent == {"channel_id": str(CHANNEL)}
    assert embed.description == f"Notification channel set to <#{CHANNEL}>"


async def test_add_notification_reports_why_it_could_not(
    backend: Backend, interaction: FakeInteraction
) -> None:
    backend.fails(404, "no such guild")

    embed = await invoke(
        notifications.add_notification, interaction, channel_id=FakeChannel(CHANNEL)
    )

    assert embed.description == "Unable to set notification channel:\n\nno such guild"


async def test_delete_notification_unsets_it(
    backend: Backend, interaction: FakeInteraction
) -> None:
    backend.replies(204)

    embed = await invoke(notifications.delete_notification, interaction)

    assert backend.called == ("DELETE", f"/guilds/{GUILD}/notification-channel")
    assert embed.description == "Notification channel unset"


async def test_delete_notification_reports_why_it_could_not(
    backend: Backend, interaction: FakeInteraction
) -> None:
    backend.fails(404, "no notification channel for guild")

    embed = await invoke(notifications.delete_notification, interaction)

    assert embed.description == (
        "Unable to unset notification channel:\n\nno notification channel for guild"
    )


async def test_list_notification_shows_the_channel(
    backend: Backend, interaction: FakeInteraction
) -> None:
    backend.replies(200, {"channel_id": str(CHANNEL)})

    embed = await invoke(notifications.list_notification, interaction)

    assert backend.called == ("GET", f"/guilds/{GUILD}/notification-channel")
    assert embed.description == f"Current notification channel: <#{CHANNEL}>"


async def test_list_notification_reports_a_failure(
    backend: Backend, interaction: FakeInteraction
) -> None:
    backend.fails(404, "no notification channel for guild")

    embed = await invoke(notifications.list_notification, interaction)

    assert embed.description == (
        "Error listing notification channel:\n\nno notification channel for guild"
    )


async def test_a_backend_that_cannot_be_reached_still_answers_the_moderator() -> None:
    """The worst case. An interaction left unanswered shows a moderator "the application
    did not respond", which tells them nothing about what to do next."""

    def refuse(_request: httpx.Request) -> httpx.Response:
        message = "connection refused"
        raise httpx.ConnectError(message)

    async with httpx.AsyncClient(
        base_url="http://backend:8000", transport=httpx.MockTransport(refuse)
    ) as client:
        unreachable = FakeInteraction(Api(client, actor=SYSTEM))

        embed = await invoke(pools.delete_pool, unreachable, pool_name="spam")

    assert is_red(embed)
    assert embed.description is not None
    assert "could not reach Timothy's backend" in embed.description


async def test_delete_ban_rejects_something_that_is_not_a_user_id(
    backend: Backend, interaction: FakeInteraction
) -> None:
    embed = await invoke(
        listings.delete_ban, interaction, user_id="<@everyone>", pool_name="spam"
    )

    assert backend.requests == []
    assert embed.description == (
        "Failed to remove `<@everyone>` from spam.\n\nthat is not a user ID"
    )


async def test_get_user_bans_rejects_something_that_is_not_a_user_id(
    backend: Backend, interaction: FakeInteraction
) -> None:
    embed = await invoke(listings.get_user_bans, interaction, user_id="0")

    assert backend.requests == []
    assert embed.description == "that is not a user ID"
