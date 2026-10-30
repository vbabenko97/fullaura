"""Signed invite sessions and the in-memory record of each browser session's checked plan."""

from backend.app.planner import Brief
from backend.app.sessions import PlanMemory, Session, read_session, sign_session
from backend.app.state import Budget, State

SECRET = b"x" * 32
BRIEF = Brief(style_goal="office")
STATE = State(budget=Budget(amount_cents=10000))


class FakeClock:
    def __init__(self) -> None:
        self.seconds = 0.0

    def __call__(self) -> float:
        return self.seconds


def test_a_session_cookie_is_valid_only_with_the_secret_that_signed_it() -> None:
    cookie = sign_session(SECRET, "invite1")
    session = read_session(SECRET, cookie)
    assert session is not None and session.invite_id == "invite1"
    assert read_session(b"y" * 32, cookie) is None
    other_invite = "invite2" + cookie.removeprefix("invite1")
    assert read_session(SECRET, other_invite) is None
    assert read_session(SECRET, None) is None and read_session(SECRET, "a.b") is None
    assert sign_session(SECRET, "invite1") != cookie  # each browser gets its own key


def test_each_new_plan_gets_a_new_token_and_the_old_one_stops_working() -> None:
    memory = PlanMemory()
    session, other = Session("invite1", "key1"), Session("invite1", "key2")
    first = memory.store(session, BRIEF, STATE, "catalog", "op-1")
    assert memory.current(session, first.token) == first
    assert memory.current(other, first.token) is None  # another browser of the same invite
    second = memory.store(session, BRIEF, STATE, "catalog", "op-2")
    assert second.version == 2
    assert memory.current(session, first.token) is None
    assert memory.current(session, None) is None
    assert memory.current(session, second.token) == second


def test_an_idle_plan_expires() -> None:
    clock = FakeClock()
    memory = PlanMemory(ttl_s=10, clock=clock)
    session = Session("invite1", "key1")
    memory.store(session, BRIEF, STATE, "catalog", None)
    clock.seconds = 9
    assert memory.get(session) is not None
    clock.seconds = 18  # the read at 9 s extended it
    assert memory.get(session) is not None
    clock.seconds = 30
    assert memory.get(session) is None


def test_a_token_or_cookie_with_other_characters_is_refused_without_an_error() -> None:
    memory = PlanMemory()
    session = Session("invite1", "key1")
    memory.store(session, BRIEF, STATE, "catalog", None)
    assert memory.current(session, "tökén") is None
    assert read_session(SECRET, "invite1.key1.mäc") is None


def test_plan_memory_keeps_a_bounded_number_of_plans_per_invite_and_in_total() -> None:
    clock = FakeClock()
    memory = PlanMemory(per_invite=2, limit=3, clock=clock)
    sessions = [Session("invite1", f"key{n}") for n in range(3)]
    for session in sessions:
        clock.seconds += 1
        memory.store(session, BRIEF, STATE, "catalog", None)
    assert memory.get(sessions[0]) is None  # the invite's oldest browser session went first
    assert memory.get(sessions[1]) and memory.get(sessions[2])
    for number in range(2):
        clock.seconds += 1
        memory.store(Session(f"other{number}", f"other-key{number}"), BRIEF, STATE, "catalog", None)
    kept = [memory.get(session) is not None for session in sessions[1:]]
    assert kept.count(True) == 1  # three plans at most in total
