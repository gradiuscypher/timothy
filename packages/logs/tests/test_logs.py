"""ADR 0014's promise, enforced rather than trusted: nothing secret reaches the file."""

import json
import logging
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

import timothy_logs as logs

TOKEN = "MTIzNDU2Nzg5MDEyMzQ1Njc4.GaBcDe.abcdefghijklmnopqrstuvwxyz1234567890"
"""Shaped like a Discord bot token, so the pattern and the exact value both apply."""


@pytest.fixture
def root() -> Iterator[logging.Logger]:
    """The root logger, restored afterwards so one test cannot configure another."""
    original = logging.getLogger().handlers[:]
    level = logging.getLogger().level
    yield logging.getLogger()
    current = logging.getLogger()
    for handler in current.handlers[:]:
        current.removeHandler(handler)
        handler.close()
    for handler in original:
        current.addHandler(handler)
    current.setLevel(level)


def read(path: Path) -> list[dict[str, object]]:
    """The JSON lines written so far."""
    return [json.loads(line) for line in path.read_text().splitlines() if line]


# -- redaction ---------------------------------------------------------------


def test_a_registered_secret_goes_wherever_it_appears() -> None:
    """The half that matters: the value, not the label in front of it.

    A bot token leaks by being interpolated into a URL or a library's exception message,
    where nothing announces it as a credential.
    """
    redactor = logs.Redactor([TOKEN])

    scrubbed = redactor.scrub(f"GET https://discord.com/api?t={TOKEN} failed")

    assert TOKEN not in scrubbed
    assert logs.REDACTED in scrubbed


def test_a_short_secret_is_ignored() -> None:
    """Registering `dev` would blank those three letters everywhere in the log."""
    redactor = logs.Redactor(["dev"])

    assert redactor.scrub("deployed to dev") == "deployed to dev"


def test_an_overlapping_secret_is_replaced_whole() -> None:
    """Longest first, or the longer value is left as a redacted fragment plus a tail."""
    redactor = logs.Redactor(["secretvalue", "secretvaluelonger"])

    assert redactor.scrub("secretvaluelonger") == logs.REDACTED


@pytest.mark.parametrize(
    "line",
    [
        "Authorization: Bearer abc123def456ghi789",
        'client_secret="hunter2hunter2"',
        "password = swordfish99",
        "api_key: sk-0123456789abcdef",
        "GET /api/auth/callback?code=abcdef123456&state=xyz HTTP/1.1",
        f"discord.py: improper token has been passed: {TOKEN}",
    ],
)
def test_credentials_are_removed_by_shape(line: str) -> None:
    """The credentials this process never held, and so could not register."""
    scrubbed = logs.Redactor().scrub(line)

    assert logs.REDACTED in scrubbed
    for leak in ("abc123def456ghi789", "hunter2hunter2", "swordfish99", "abcdef123456"):
        assert leak not in scrubbed


def test_a_label_survives_its_value() -> None:
    """`client_secret=[REDACTED]` is worth far more when debugging than a blank."""
    assert logs.Redactor().scrub("client_secret=hunter2hunter2") == (
        f"client_secret={logs.REDACTED}"
    )


def test_snowflakes_survive() -> None:
    """Redaction is over-broad on purpose, but not so broad that it eats the IDs every
    trace is followed by."""
    line = "banned user 300000000000000001 in guild 100000000000000002"

    assert logs.Redactor([TOKEN]).scrub(line) == line


# -- what actually reaches the file ------------------------------------------


def test_the_file_holds_json_lines(tmp_path: Path, root: logging.Logger) -> None:
    logs.configure("test", level="INFO", log_dir=tmp_path)

    logging.getLogger("timothy.example").info("hello %s", "world", extra={"guild_id": 7})

    (entry,) = read(tmp_path / "test.log")
    assert entry["level"] == "INFO"
    assert entry["service"] == "test"
    assert entry["logger"] == "timothy.example"
    assert entry["message"] == "hello world"
    assert entry["extra"] == {"guild_id": 7}


def test_a_traceback_reaches_the_file(tmp_path: Path, root: logging.Logger) -> None:
    """The whole point of a durable log: the exception, not just the line before it."""
    logs.configure("test", log_dir=tmp_path)

    try:
        raise ValueError("nope")  # noqa: EM101, TRY301
    except ValueError:
        logging.getLogger("timothy.example").exception("it broke")

    (entry,) = read(tmp_path / "test.log")
    assert "ValueError: nope" in str(entry["exception"])


def test_a_secret_in_a_traceback_does_not_reach_the_file(
    tmp_path: Path, root: logging.Logger
) -> None:
    """Why redaction is in the formatter and not in a filter.

    A `logging.Filter` sees `msg` and `args` and never sees this — and the repr of the
    arguments to the frame that raised is exactly where a credential turns up.
    """
    logs.configure("test", log_dir=tmp_path, secrets=[TOKEN])

    def login(token: str) -> None:
        message = f"401 Unauthorized for token {token}"
        raise RuntimeError(message)

    try:
        login(TOKEN)
    except RuntimeError:
        logging.getLogger("timothy.example").exception("login failed")

    written = (tmp_path / "test.log").read_text()
    assert TOKEN not in written
    assert logs.REDACTED in written


def test_a_secret_never_reaches_the_console_either(
    tmp_path: Path, root: logging.Logger, capsys: pytest.CaptureFixture[str]
) -> None:
    logs.configure("test", log_dir=tmp_path, secrets=[TOKEN])

    logging.getLogger("timothy.example").info("connecting with %s", TOKEN)

    assert TOKEN not in capsys.readouterr().out


def test_a_secret_discovered_later_can_be_registered(
    tmp_path: Path, root: logging.Logger
) -> None:
    """`configure` hands back its redactor so a credential read after startup is
    covered too."""
    redactor = logs.configure("test", log_dir=tmp_path)
    redactor.add("late-discovered-credential")

    logging.getLogger("timothy.example").info("using late-discovered-credential")

    assert "late-discovered-credential" not in (tmp_path / "test.log").read_text()


def test_a_second_call_does_not_double_every_line(tmp_path: Path, root: logging.Logger) -> None:
    logs.configure("test", log_dir=tmp_path)
    logs.configure("test", log_dir=tmp_path)

    logging.getLogger("timothy.example").info("once")

    assert len(read(tmp_path / "test.log")) == 1


def test_an_unwritable_log_directory_does_not_stop_the_process(
    tmp_path: Path, root: logging.Logger, capsys: pytest.CaptureFixture[str]
) -> None:
    """A broken log path must not be able to take enforcement down with it."""
    blocked = tmp_path / "file"
    blocked.write_text("not a directory")

    logs.configure("test", log_dir=blocked / "logs")
    logging.getLogger("timothy.example").info("still running")

    assert "still running" in capsys.readouterr().out


def test_no_log_directory_leaves_the_console(
    root: logging.Logger, capsys: pytest.CaptureFixture[str]
) -> None:
    logs.configure("test", log_dir=None)

    logging.getLogger("timothy.example").info("stdout only")

    assert "stdout only" in capsys.readouterr().out


def test_console_format_writes_a_sentence(
    tmp_path: Path, root: logging.Logger, capsys: pytest.CaptureFixture[str]
) -> None:
    """The default, for a person running a bare process outside compose."""
    logs.configure("test", log_dir=tmp_path, log_format="console")

    logging.getLogger("timothy.example").info("readable by eye")

    line = capsys.readouterr().out.strip()
    assert "timothy.example: readable by eye" in line
    with pytest.raises(json.JSONDecodeError):
        json.loads(line)


def test_json_format_writes_one_object_per_line(
    tmp_path: Path, root: logging.Logger, capsys: pytest.CaptureFixture[str]
) -> None:
    """What compose sets, and what the collector parses into fields.

    The three field names asserted here are the ones VictoriaLogs is told to index —
    `ts` as `_time`, `message` as `_msg`, `service` as a stream field (ADR 0015).
    Renaming any of them silently unshapes everything in the store, so they are pinned
    by a test rather than by a comment.
    """
    logs.configure("test", log_dir=tmp_path, log_format="json")

    logging.getLogger("timothy.example").info("readable by machine", extra={"guild_id": 7})

    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["message"] == "readable by machine"
    assert payload["service"] == "test"
    assert payload["ts"]
    assert payload["extra"]["guild_id"] == 7


def test_an_unrecognised_format_falls_back_to_the_console(
    tmp_path: Path, root: logging.Logger, capsys: pytest.CaptureFixture[str]
) -> None:
    """A typo in TIMOTHY_LOG_FORMAT must not be the thing that stops the process — the
    logs are how a bad configuration explains itself."""
    logs.configure("test", log_dir=tmp_path, log_format="jsno")

    logging.getLogger("timothy.example").info("still running")

    assert "still running" in capsys.readouterr().out


def test_a_secret_never_reaches_stdout_as_json_either(
    tmp_path: Path, root: logging.Logger, capsys: pytest.CaptureFixture[str]
) -> None:
    """Redaction is the half of ADR 0014 that survives, and it matters more here: an
    unredacted line now lands in an indexed, queryable store rather than a flat file."""
    logs.configure("test", log_dir=tmp_path, log_format="json", secrets=[TOKEN])

    logging.getLogger("timothy.example").info("connecting with %s", TOKEN)

    out = capsys.readouterr().out
    assert TOKEN not in out
    assert logs.REDACTED in json.loads(out.strip())["message"]


def test_an_unhandled_exception_is_logged_rather_than_printed(
    tmp_path: Path, root: logging.Logger
) -> None:
    """Python's default writes this to stderr, which the daemon throws away with the
    container. It is also the last thing a dying process says."""
    logs.configure("test", log_dir=tmp_path)

    try:
        raise KeyError("gone")  # noqa: EM101, TRY301
    except KeyError as error:
        sys.excepthook(type(error), error, error.__traceback__)

    (entry,) = read(tmp_path / "test.log")
    assert entry["level"] == "CRITICAL"
    assert "KeyError" in str(entry["exception"])
