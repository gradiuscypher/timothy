"""Ask Discord what this token actually is, and what it can actually reach.

Three questions that all fail as the same `50001 Missing Access` when the bot cannot
upload its commands to the management guild, and which the error alone cannot tell apart:

1. Which application does `TIMOTHY_DISCORD_TOKEN` belong to? A staging token against a
   production guild looks exactly like a permissions problem.
2. Is the bot in `TIMOTHY_MANAGEMENT_GUILD_ID` at all?
3. Is the *application* authorised for commands there? That is the `applications.commands`
   scope, which is granted by the invite URL and is not one of the bot's permissions —
   the distinction that makes "I re-invited it and nothing changed" so common.

Run it against a running stack, where it picks up the real environment::

    docker compose run --rm --no-deps -T --entrypoint python bot - \
        < scripts/check-discord-access.py

Read-only. The call that would be destructive — `PUT`-ing the command list — is
deliberately a `GET` here, because a `PUT` with an empty body deletes every guild command.
"""

import asyncio
import os

import httpx

API = "https://discord.com/api/v10"


async def main() -> None:
    """Print what the token is, where the bot is, and whether commands are allowed."""
    token = os.environ.get("TIMOTHY_DISCORD_TOKEN", "")
    wanted = os.environ.get("TIMOTHY_MANAGEMENT_GUILD_ID", "")
    print(f"TIMOTHY_MANAGEMENT_GUILD_ID = {wanted!r}\n")

    async with httpx.AsyncClient(
        base_url=API, headers={"Authorization": f"Bot {token}"}, timeout=15.0
    ) as client:
        response = await client.get("/oauth2/applications/@me")
        if not response.is_success:
            print(f"the bot token was refused: {response.status_code} {response.text[:200]}")
            return
        app = response.json()
        print(f"this token is the bot of application {app.get('name')!r} (id {app.get('id')})")

        response = await client.get("/users/@me/guilds")
        if not response.is_success:
            print(f"could not list guilds: {response.status_code} {response.text[:200]}")
            return
        guilds = response.json()

        print(f"\nthe bot is in {len(guilds)} guild(s):")
        for guild in guilds:
            here = " <== TIMOTHY_MANAGEMENT_GUILD_ID" if guild["id"] == wanted else ""
            print(f"  {guild['id']}  {guild['name']}{here}")

        if not any(guild["id"] == wanted for guild in guilds):
            print(f"\n>>> this bot is NOT in {wanted}.")
            print(">>> Either the ID names a different guild, or the bot you invited")
            print(">>> belongs to a different application than this token.")
            return

        response = await client.get(f"/applications/{app['id']}/guilds/{wanted}/commands")
        if response.is_success:
            print(f"\ncommand access OK — {len(response.json())} guild command(s) registered.")
            print("If the bot still fails to sync, the failure is not this.")
        else:
            print(f"\n>>> command access REFUSED: {response.status_code} {response.text[:200]}")
            print(">>> The bot is in the guild, but the APPLICATION is not authorised")
            print(">>> there for commands. That is the applications.commands scope, and")
            print(">>> it is granted by the invite URL, not by the bot's permissions.")
            print(">>> Re-invite with a URL that literally contains:")
            print(">>>   scope=bot+applications.commands")


asyncio.run(main())
