"""Names for the IDs the UI shows, and where they come from.

The cache exists so a page can say whom a row is about. Two properties are what the tests
below are for: it is filled only from traffic Timothy already has — a login and a relayed
gateway event — and nothing anywhere decides anything from it. An ID nobody has seen a
name for resolves to nothing at all, which is a different answer from "called nothing".
"""

from fastapi.testclient import TestClient

from .conftest import (
    GUILD,
    LISTED_USER,
    MEMBER,
    OUTSIDER,
    FakeOAuth,
    headers,
    sign_in,
)


def resolve(client: TestClient, *user_ids: int, actor: int | str = MEMBER) -> dict[str, str]:
    """Ask for these IDs, and read the answer back as `{id: name}`."""
    response = client.get(
        "/users/names",
        params=[("id", str(user_id)) for user_id in user_ids],
        headers=headers(actor),
    )
    assert response.status_code == 200, response.text
    return {row["user_id"]: row["name"] for row in response.json()}


def relay(client: TestClient, path: str, *, user_id: int, username: str | None) -> None:
    body: dict[str, str] = {"guild_id": str(GUILD), "user_id": str(user_id)}
    if username is not None:
        body["username"] = username
    response = client.post(f"/events/{path}", json=body, headers=headers("system"))
    assert response.status_code == 202, response.text


# -- what fills it -----------------------------------------------------------


def test_a_join_teaches_timothy_a_name(registered: TestClient) -> None:
    """The gateway has the name already; the backend has no other way to learn it."""
    relay(registered, "member-join", user_id=LISTED_USER, username="Nuisance")

    assert resolve(registered, LISTED_USER) == {str(LISTED_USER): "Nuisance"}


def test_an_unban_teaches_timothy_a_name(registered: TestClient) -> None:
    relay(registered, "ban-remove", user_id=LISTED_USER, username="Nuisance")

    assert resolve(registered, LISTED_USER) == {str(LISTED_USER): "Nuisance"}


def test_signing_in_records_the_name_discord_just_gave(
    registered: TestClient, oauth: FakeOAuth
) -> None:
    """The people reading these pages are the people most often named on them: the actor
    on an audit entry, the author of a listing."""
    sign_in(registered, oauth, user_id=MEMBER, username="mod")

    assert resolve(registered, MEMBER) == {str(MEMBER): "mod"}


def test_a_later_sighting_replaces_an_earlier_one(registered: TestClient) -> None:
    """The column is what a user is called *now*, not a history of what they have been."""
    relay(registered, "member-join", user_id=LISTED_USER, username="Nuisance")
    relay(registered, "member-join", user_id=LISTED_USER, username="Renamed")

    assert resolve(registered, LISTED_USER) == {str(LISTED_USER): "Renamed"}


def test_an_event_without_a_name_is_relayed_exactly_as_before(registered: TestClient) -> None:
    """A bot too old to send one still relays events; there is simply nothing to learn."""
    relay(registered, "member-join", user_id=LISTED_USER, username=None)

    assert resolve(registered, LISTED_USER) == {}


def test_a_blank_name_does_not_erase_a_known_one(registered: TestClient) -> None:
    """Storing it would put the page back to showing a bare ID, which is worse than
    keeping the last name that meant something."""
    relay(registered, "member-join", user_id=LISTED_USER, username="Nuisance")
    relay(registered, "member-join", user_id=LISTED_USER, username="   ")

    assert resolve(registered, LISTED_USER) == {str(LISTED_USER): "Nuisance"}


def test_a_name_survives_the_session_that_taught_it(
    registered: TestClient, oauth: FakeOAuth
) -> None:
    """The ID outlives the login. Logging out revokes a session, not a person's name."""
    sign_in(registered, oauth, user_id=MEMBER, username="mod")
    registered.post("/auth/logout", headers={"Origin": "http://testserver"})

    assert resolve(registered, MEMBER) == {str(MEMBER): "mod"}


# -- what reading it looks like ----------------------------------------------


def test_ids_with_no_name_are_absent_rather_than_empty(registered: TestClient) -> None:
    """ "Never seen" and "called nothing" are different, and the caller shows the ID for
    the first of them."""
    relay(registered, "member-join", user_id=LISTED_USER, username="Nuisance")

    assert resolve(registered, LISTED_USER, OUTSIDER) == {str(LISTED_USER): "Nuisance"}


def test_asking_about_nobody_is_an_empty_answer(registered: TestClient) -> None:
    """The UI hands over whatever is on the page, and an empty page is a normal page."""
    assert resolve(registered) == {}


def test_a_name_is_readable_by_anyone_who_can_read_a_listing(
    registered: TestClient,
) -> None:
    """A name is not a further disclosure: it is what Discord calls a public account."""
    relay(registered, "member-join", user_id=LISTED_USER, username="Nuisance")

    assert resolve(registered, LISTED_USER, actor=MEMBER) == {str(LISTED_USER): "Nuisance"}


def test_someone_in_no_guild_timothy_is_in_gets_nothing(registered: TestClient) -> None:
    response = registered.get(
        "/users/names",
        params=[("id", str(LISTED_USER))],
        headers=headers(OUTSIDER),
    )

    assert response.status_code == 403, response.text


def test_asking_about_too_many_ids_is_refused(registered: TestClient) -> None:
    """The cap is what keeps a hand-written query from asking for the whole table."""
    response = registered.get(
        "/users/names",
        params=[("id", str(LISTED_USER + offset)) for offset in range(201)],
        headers=headers(MEMBER),
    )

    assert response.status_code == 422, response.text
