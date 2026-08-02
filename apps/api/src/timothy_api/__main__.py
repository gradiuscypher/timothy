"""Entry point: `timothy-api`."""

import uvicorn

from timothy_api.settings import Settings


def main() -> None:
    """Serve the API."""
    settings = Settings()
    uvicorn.run(
        "timothy_api.app:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
    )


if __name__ == "__main__":
    main()
