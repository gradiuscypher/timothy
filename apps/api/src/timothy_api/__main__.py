"""Entry point: `timothy-api`.

Uvicorn configures its own loggers and leaves the root logger alone, so without the
`basicConfig` here every line Timothy logs about its own work — the worker starting, a
guild the circuit breaker paused, a job abandoned after its last attempt — is written to
a handler that does not exist. The API answered, the container looked healthy, and the
enforcement side was silent. Found by reading the logs of a running stack and not finding
anything in them.
"""

import logging

import uvicorn

from timothy_api.settings import Settings


def main() -> None:
    """Serve the API."""
    settings = Settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    uvicorn.run(
        "timothy_api.app:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
    )


if __name__ == "__main__":
    main()
