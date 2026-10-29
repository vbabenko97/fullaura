"""Anonymous demo session age and request limits."""

from backend.app.sessions import DemoSessionStore


class FakeClock:
    def __init__(self) -> None:
        self.seconds = 0.0

    def __call__(self) -> float:
        return self.seconds

    def advance(self, seconds: float) -> None:
        self.seconds += seconds


def test_planning_requires_age_confirmation_and_enforces_a_session_limit() -> None:
    clock = FakeClock()
    sessions = DemoSessionStore(plan_request_limit=2, clock=clock)
    session_id, initial = sessions.get_or_create(None)

    assert initial.age_confirmed is False
    assert sessions.check_plan_access(session_id)[0] == "age_required"
    assert sessions.reserve_plan_request(session_id)[0] == "age_required"
    confirmed = sessions.confirm_age(session_id)
    assert confirmed is not None and confirmed.age_confirmed is True

    first, first_view = sessions.reserve_plan_request(session_id)
    second, second_view = sessions.reserve_plan_request(session_id)
    third, third_view = sessions.reserve_plan_request(session_id)
    assert (first, second, third) == ("ok", "ok", "limit")
    assert first_view is not None and first_view.plan_requests_remaining == 1
    assert second_view is not None and second_view.plan_requests_remaining == 0
    assert third_view is not None and third_view.plan_requests_used == 2

    another_id, another_session = sessions.get_or_create(None)
    assert another_id != session_id
    assert another_session.age_confirmed is False
    assert another_session.plan_requests_remaining == 2


def test_inactive_session_expires_and_restarts_with_a_fresh_quota() -> None:
    clock = FakeClock()
    sessions = DemoSessionStore(ttl_seconds=10, plan_request_limit=2, clock=clock)
    session_id, _ = sessions.get_or_create(None)
    assert sessions.confirm_age(session_id) is not None
    assert sessions.reserve_plan_request(session_id)[0] == "ok"

    clock.advance(10)
    assert sessions.check_plan_access(session_id) == ("missing", None)

    new_id, new_session = sessions.get_or_create(session_id)
    assert new_id != session_id
    assert new_session.age_confirmed is False
    assert new_session.plan_requests_used == 0
    assert new_session.plan_requests_remaining == 2
