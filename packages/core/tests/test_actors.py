import pytest

from timothy_core.actors import Actor


def test_a_user_actor_round_trips() -> None:
    actor = Actor.user(1234567890123456789)

    assert str(actor) == "user:1234567890123456789"
    assert Actor.parse(str(actor)) == actor
    assert not actor.is_system


def test_the_system_actor_round_trips() -> None:
    actor = Actor.system()

    assert str(actor) == "system"
    assert Actor.parse("system") == actor
    assert actor.is_system


def test_the_system_actor_cannot_be_confused_with_a_user() -> None:
    """The old bot attributed its own actions to user "0", which read as a real user."""
    assert Actor.system() != Actor.user(0)
    assert str(Actor.system()) != str(Actor.user(0))


@pytest.mark.parametrize("raw", ["", "0", "1234", "user:", "user:abc", "User:1", "system "])
def test_junk_does_not_parse(raw: str) -> None:
    with pytest.raises(ValueError, match="not an actor"):
        Actor.parse(raw)
