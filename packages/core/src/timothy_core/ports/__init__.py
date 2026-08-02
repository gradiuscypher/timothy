"""The boundaries between Timothy's domain and the outside world.

Today there is exactly one: Discord. The domain layer never imports `discord.py`
(ADR 0007) — it depends on the protocol here, and the adapter that satisfies it in
production lives in the backend, next to the bot token.
"""
