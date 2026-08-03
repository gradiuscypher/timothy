"""Entry point: `timothy-api`.

Uvicorn configures its own loggers and leaves the root logger alone, so without the
setup here every line Timothy logs about its own work — the worker starting, a guild the
circuit breaker paused, a job abandoned after its last attempt — is written to a handler
that does not exist. The API answered, the container looked healthy, and the enforcement
side was silent. Found by reading the logs of a running stack and not finding anything in
them.

`log_config=None` is the other half of that. Uvicorn's default configuration installs
handlers on `uvicorn`, `uvicorn.error` and `uvicorn.access` and turns off their
propagation, which would route the access log and every ASGI traceback past the file
handler :mod:`timothy_logs` just installed. Passing `None` leaves those loggers
bare, so everything uvicorn says lands in the same place as everything Timothy says.
"""

import uvicorn

import timothy_logs as logs
from timothy_api.settings import Settings


def main() -> None:
    """Serve the API."""
    settings = Settings()
    logs.configure(
        "backend",
        level=settings.log_level,
        log_dir=settings.log_dir,
        log_format=settings.log_format,
        # Everything this process holds that must never reach a log file. The Discord
        # token and the internal token both travel in headers that libraries below us
        # will happily include in an error message; the client secret is posted to
        # Discord during login.
        secrets=(
            settings.discord_token.get_secret_value(),
            settings.internal_token.get_secret_value(),
            settings.discord_client_secret.get_secret_value(),
        ),
    )
    uvicorn.run(
        "timothy_api.app:app",
        host=settings.host,
        port=settings.port,
        log_config=None,
    )


if __name__ == "__main__":
    main()
