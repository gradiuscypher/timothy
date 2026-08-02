"""What the old bot would have done, reimplemented from its source.

This is the reference the cutover is checked against, so it is a transcription and not an
improvement. Where the old code was wrong it is wrong here too; where it was surprising it
is surprising here too. The corresponding Rust is `db_wrapper/src/mongo.rs` in
`banpool-tim-gcp` — `is_user_banned_on_guild`, `is_guild_subscribed`, `is_user_exception`
— and `bot/src/bin/bot.rs`, which is the only thing that called it in production.

Three properties of that code are the whole reason this module exists:

**`global` was a short-circuit, not a subscription.** `is_guild_subscribed` returned
`true` for the pool named `global` without looking anything up. No row ever existed, and
no guild could leave. ADR 0002 undoes that, which is why the import materialises rows —
and why the check has to model the old behaviour to know whether it succeeded.

**The live ban check never read `subscription_level`.** `is_user_banned_on_guild` asks
`is_guild_subscribed` and then bans. A guild that set a pool to `warn` was getting bans
from that pool on every member join, and only the offline `tools.rs` sync — run by hand,
rarely — respected the level. So `warn` in the new world is a real behaviour change for
those guilds, and :mod:`timothy_migration.check` classifies it as an expected one rather
than burying it in a count.

**Exceptions were checked after the pool match and applied guild-wide.** Same as ADR 0006
leaves them, so this part carries across unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterable

    from timothy_migration.records import Source

GLOBAL_POOL: Final = "global"
"""The name `is_guild_subscribed` short-circuited on, hardcoded in the old bot exactly
like this."""


@dataclass(frozen=True, slots=True)
class OldBot:
    """The old bot's data, indexed for the one question worth asking of it.

    Built from the parsed dump rather than from the imported database on purpose: it has
    to be able to disagree with the import, which it cannot do if it reads the import's
    output.
    """

    pools_by_user: dict[int, frozenset[str]]
    subscribed_pools: dict[int, frozenset[str]]
    exceptions: frozenset[tuple[int, int]]

    @classmethod
    def from_source(cls, source: Source) -> OldBot:
        """Index a parsed dump.

        Orphaned listings — bans in pools that `delete_pool` removed without cascading —
        are kept, because the old bot kept them too: `get_user_bans` queried `bans` by
        user and never joined to `banpools`, so a ban in a deleted pool still counted as
        long as some guild was somehow subscribed to that name. Dropping them here would
        have the check agree with an import that had silently lost them.
        """
        pools_by_user: dict[int, set[str]] = {}
        for listing in source.listings:
            pools_by_user.setdefault(listing.user_id, set()).add(listing.pool_name)

        subscribed: dict[int, set[str]] = {}
        for subscription in source.subscriptions:
            subscribed.setdefault(subscription.guild_id, set()).add(subscription.pool_name)

        # The short-circuit, made explicit. Every guild is subscribed to `global`,
        # including guilds that appear nowhere in the dump — hence `subscribes` below
        # rather than a lookup that could miss.
        return cls(
            pools_by_user={
                user_id: frozenset(names) for user_id, names in pools_by_user.items()
            },
            subscribed_pools={
                guild_id: frozenset(names) for guild_id, names in subscribed.items()
            },
            exceptions=frozenset(
                (exception.guild_id, exception.user_id) for exception in source.exceptions
            ),
        )

    def subscribes(self, guild_id: int, pool_name: str) -> bool:
        """`is_guild_subscribed`, short-circuit and all."""
        if pool_name == GLOBAL_POOL:
            return True
        return pool_name in self.subscribed_pools.get(guild_id, frozenset())

    def has_exception(self, guild_id: int, user_id: int) -> bool:
        """`is_user_exception`."""
        return (guild_id, user_id) in self.exceptions

    def would_ban(self, *, guild_id: int, user_id: int) -> bool:
        """`is_user_banned_on_guild`, as a yes or no.

        The old function returned the reason string of whichever listing matched first,
        in whatever order Mongo happened to return the documents. Which pool got the
        credit was therefore not deterministic, so the check compares the decision and
        not the attribution.
        """
        return bool(self.justifying_pools(guild_id=guild_id, user_id=user_id))

    def justifying_pools(self, *, guild_id: int, user_id: int) -> frozenset[str]:
        """Every pool that would have made the old bot ban this user in this guild.

        Empty when it would not have, for any reason — not listed, not subscribed, or
        excepted. The old bot could not tell those apart either; it returned `None`.
        """
        if self.has_exception(guild_id, user_id):
            return frozenset()
        return frozenset(
            pool_name
            for pool_name in self.pools_by_user.get(user_id, frozenset())
            if self.subscribes(guild_id, pool_name)
        )

    def enforced_pairs(self, guild_ids: Iterable[int]) -> frozenset[tuple[int, int]]:
        """Every (guild, user) the old bot would ban, across `guild_ids`.

        This is a decision set, not a prediction of bans: enforcement is reactive on both
        sides of the cutover, so a pair here is banned when that user is in that guild and
        not before. What it is good for is comparison — the same computation run against
        the imported database has to produce the same set, or the import changed who is
        enforced against whom.

        Cost is guilds times listed users, which for the real data is a few hundred by a
        few tens of thousands. Seconds, once, on a machine that is not otherwise busy.
        """
        listed = sorted(self.pools_by_user)
        return frozenset(
            (guild_id, user_id)
            for guild_id in guild_ids
            for user_id in listed
            if self.would_ban(guild_id=guild_id, user_id=user_id)
        )
