"""The shopping agent over HTTP: the stream of steps, quotas, cost, repeats and the try-on."""

import json
import threading
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.pilot import MAX_RECENT_RETURNS
from backend.app.shopper import (
    FIND_RESERVE_MICRO_USD,
    Find,
    FindOutcome,
    FindRequest,
    FindResult,
    FindSlot,
    FindUsage,
    ShopAgent,
)
from backend.app.shops import (
    FAILURES_TO_STOP,
    RETRY_FAILED_PAGES_S,
    Shop,
    ShopIndex,
    ShopProduct,
)
from backend.app.store import MemoryStore
from scripts.pilot_report import OUTCOME_CATEGORY
from tests.test_api import OTHER_CODE, FakeEditor, join, jpeg, settings
from tests.test_shopper import FakeModel, answer, call
from tests.test_shops import SHOP, listing, offer, page, product, serve

JACKET = ShopProduct(
    "https://shop.example/p/jacke", "Shop", "Winterjacke", 9000, "schwarz", None, False
)
BODY = {"request": "A warm jacket", "scope": "item", "line": "men", "budget_cents": 15000}


class FakeAgent:
    def __init__(self, result: FindResult | Exception | None = None) -> None:
        self.result = result or FindResult(
            FindOutcome.SUCCESS,
            [Find(JACKET, FindSlot.OUTER_LAYER, "Warm enough for Vienna")],
            usage=FindUsage(fresh_tokens=1000, output_tokens=100),
        )
        self.requests: list[FindRequest] = []

    def __call__(self, request: FindRequest, on_step: Callable[[str], None]) -> FindResult:
        self.requests.append(request)
        on_step("Looking in the shops for: jacke")
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture
def agent() -> FakeAgent:
    return FakeAgent()


@pytest.fixture
def store() -> MemoryStore:
    return MemoryStore()


@pytest.fixture
def editor() -> FakeEditor:
    return FakeEditor()


@pytest.fixture
def http(
    tmp_path: Path, agent: FakeAgent, store: MemoryStore, editor: FakeEditor
) -> Iterator[TestClient]:
    app = create_app(settings(tmp_path), image_editor=editor, store=store, shop_agent=agent)
    with TestClient(app) as client:
        yield client


def find(http: TestClient, body: dict[str, Any] | None = None, operation: str | None = None) -> Any:
    return http.post(
        "/api/finds", json=body or BODY, headers={"X-Operation-Id": operation or str(uuid.uuid4())}
    )


def lines(response: Any) -> list[dict[str, Any]]:
    return [json.loads(line) for line in response.text.splitlines()]


def session(http: TestClient) -> dict[str, Any]:
    return http.get("/api/session").json()


def test_the_answer_streams_steps_then_a_result_with_a_token(http: TestClient) -> None:
    join(http)
    assert session(http)["find_open"] is True
    response = find(http)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert response.headers["cache-control"] == "no-store"
    answer = lines(response)
    assert answer[0] == {"step": "Looking in the shops for: jacke"}
    result = answer[-1]["result"]
    assert result["outcome"] == "success"
    assert result["find_token"]
    assert result["total_cents"] == 9000
    assert result["finds"][0]["url"] == JACKET.url
    assert result["finds"][0]["size_checked"] is False
    assert session(http)["plan_requests_used"] == 1


def test_finds_need_an_invite_and_an_age_confirmation(http: TestClient, agent: FakeAgent) -> None:
    assert find(http).status_code == 401
    assert http.post("/api/session/invite", json={"code": OTHER_CODE}).status_code == 200
    assert find(http).status_code == 403
    assert agent.requests == []


def test_a_request_needs_an_operation_id_and_valid_fields(
    http: TestClient, agent: FakeAgent
) -> None:
    join(http)
    assert http.post("/api/finds", json=BODY).status_code == 400
    assert find(http, {**BODY, "budget_cents": 0}).status_code == 422
    assert find(http, {**BODY, "scope": "everything"}).status_code == 422
    assert find(http, {**BODY, "plan": {}}).status_code == 422
    assert agent.requests == []


def test_a_repeated_operation_answers_again_for_free(http: TestClient, agent: FakeAgent) -> None:
    join(http)
    operation = str(uuid.uuid4())
    first = lines(find(http, operation=operation))[-1]["result"]
    second = lines(find(http, operation=operation))[-1]["result"]
    assert first == second
    assert len(agent.requests) == 1
    assert session(http)["plan_requests_used"] == 1


class HeldAgent(FakeAgent):
    """An agent that does not answer until the test lets it."""

    def __init__(self, result: FindResult | None = None) -> None:
        super().__init__(result)
        self.started = threading.Event()
        self.release = threading.Event()

    def __call__(self, request: FindRequest, on_step: Callable[[str], None]) -> FindResult:
        self.started.set()
        assert self.release.wait(10)
        return super().__call__(request, on_step)


@pytest.mark.parametrize(
    ("result", "products"),
    [(None, 1), (FindResult(FindOutcome.NO_MATCH, message="Nothing warm enough."), 0)],
)
def test_a_page_reloaded_during_a_search_can_collect_its_answer_for_free(
    tmp_path: Path, result: FindResult | None, products: int
) -> None:
    agent = HeldAgent(result)
    operation = str(uuid.uuid4())
    with TestClient(create_app(settings(tmp_path), shop_agent=agent)) as client:
        join(client)
        first = threading.Thread(target=find, args=(client,), kwargs={"operation": operation})
        first.start()
        assert agent.started.wait(10)

        running = client.get(f"/api/finds/{operation}")
        assert running.status_code == 200
        assert running.headers["cache-control"] == "no-store"
        assert running.json() == {"running": True}

        agent.release.set()
        first.join(10)
        answered = client.get(f"/api/finds/{operation}").json()["result"]
        assert len(answered["finds"]) == products
        assert bool(answered["find_token"]) is bool(products)
        assert len(agent.requests) == 1
        assert session(client)["plan_requests_used"] == 1


def test_a_search_can_be_collected_only_by_its_session_and_operation(
    http: TestClient, tmp_path: Path
) -> None:
    join(http)
    operation = str(uuid.uuid4())
    lines(find(http, operation=operation))
    unknown = http.get(f"/api/finds/{uuid.uuid4()}")
    assert unknown.status_code == 404
    assert unknown.json()["reason"] == "find_unknown"
    assert http.get(f"/api/finds/{operation}").status_code == 200
    # A later search takes the earlier one's place, as the page then shows only the later one.
    lines(find(http))
    assert http.get(f"/api/finds/{operation}").status_code == 404
    http.cookies.clear()
    assert http.get(f"/api/finds/{operation}").status_code == 401


def test_the_quota_ends_finds(tmp_path: Path, agent: FakeAgent) -> None:
    app = create_app(settings(tmp_path, plan_requests_per_invite=1), shop_agent=agent)
    with TestClient(app) as client:
        join(client)
        assert find(client).status_code == 200
        assert session(client)["find_open"] is False
        refused = find(client)
        assert refused.status_code == 429
        assert refused.json()["reason"] == "quota"
    assert len(agent.requests) == 1


def test_a_small_budget_closes_finds(tmp_path: Path, agent: FakeAgent) -> None:
    budget = (FIND_RESERVE_MICRO_USD - 1) / 1_000_000
    app = create_app(settings(tmp_path, planning_budget_usd=budget), shop_agent=agent)
    with TestClient(app) as client:
        join(client)
        assert session(client)["find_open"] is False
        assert find(client).json()["reason"] == "budget"


def test_the_mode_can_be_turned_off(tmp_path: Path, agent: FakeAgent) -> None:
    app = create_app(settings(tmp_path, shop_mode=False), shop_agent=agent)
    with TestClient(app) as client:
        join(client)
        assert session(client)["find_open"] is False
        assert find(client).status_code == 404
    assert agent.requests == []


def test_no_key_means_the_mode_is_not_offered(tmp_path: Path) -> None:
    with TestClient(create_app(settings(tmp_path))) as client:
        join(client)
        assert session(client)["find_open"] is False
        assert find(client).json()["reason"] == "not_configured"


@pytest.mark.parametrize(
    "broken",
    [
        RuntimeError("secret detail"),
        FindResult(FindOutcome.FAILED, message="The model could not be reached.", cost_known=False),
    ],
)
def test_a_failed_search_answers_without_detail_and_keeps_its_reservation(
    tmp_path: Path, store: MemoryStore, broken: Any
) -> None:
    app = create_app(settings(tmp_path), store=store, shop_agent=FakeAgent(broken))
    with TestClient(app) as client:
        join(client)
        result = lines(find(client))[-1]["result"]
        assert result["outcome"] == "failed"
        assert result["find_token"] is None
        assert result["finds"] == []
        assert "secret" not in str(result["message"])
        assert session(client)["plan_requests_used"] == 1
    records = [store.read(name) for name in store.names() if name.startswith("operations/")]
    assert [(document.data["status"], document.data["kind"]) for document in records if document]
    assert all(document.data["outcome"] in {"error", "failed"} for document in records if document)


def test_the_try_on_is_written_from_what_was_found(http: TestClient, editor: FakeEditor) -> None:
    join(http, photos=True)
    token = lines(find(http))[-1]["result"]["find_token"]
    response = http.post(
        "/api/previews",
        files={
            "portrait": ("portrait.jpg", jpeg(), "image/jpeg"),
            "full_body": ("full-body.jpg", jpeg(), "image/jpeg"),
        },
        headers={"X-Find-Token": token, "X-Operation-Id": str(uuid.uuid4())},
    )
    assert response.status_code == 200
    assert 'outer_layer: described as "schwarz, Winterjacke"' in editor.prompts[0]
    assert "the clothes they wear in the full-body photo" in editor.prompts[0]


def test_a_new_search_retires_the_token(http: TestClient, editor: FakeEditor) -> None:
    join(http, photos=True)
    first = lines(find(http))[-1]["result"]["find_token"]
    second = lines(find(http))[-1]["result"]["find_token"]
    assert first != second
    response = http.post(
        "/api/previews",
        files={
            "portrait": ("portrait.jpg", jpeg(), "image/jpeg"),
            "full_body": ("full-body.jpg", jpeg(), "image/jpeg"),
        },
        headers={"X-Find-Token": first, "X-Operation-Id": str(uuid.uuid4())},
    )
    assert response.status_code == 409
    assert response.json()["reason"] == "find_expired"
    assert editor.prompts == []


class NotReadyAgent(FakeAgent):
    def ready(self) -> bool:
        return False


def test_shops_that_cannot_be_read_count_nothing(tmp_path: Path) -> None:
    agent = NotReadyAgent()
    with TestClient(create_app(settings(tmp_path), shop_agent=agent)) as client:
        join(client)
        refused = find(client)
        assert refused.status_code == 503
        assert refused.json()["reason"] == "shops_unavailable"
        assert session(client)["plan_requests_used"] == 0
    assert agent.requests == []


SHOPS_DOWN = FindResult(
    FindOutcome.SHOPS_UNAVAILABLE,
    message="The shops could not be read right now. Try again later.",
    usage=FindUsage(fresh_tokens=100, output_tokens=10),
)


def test_shop_pages_that_all_fail_give_the_request_back_and_keep_the_cost(
    tmp_path: Path, store: MemoryStore
) -> None:
    app = create_app(settings(tmp_path), store=store, shop_agent=FakeAgent(SHOPS_DOWN))
    operation = str(uuid.uuid4())
    with TestClient(app) as client:
        join(client)
        result = lines(find(client, operation=operation))[-1]["result"]
        assert result["outcome"] == "shops_unavailable"
        assert result["counted"] is False
        assert result["find_token"] is None
        assert session(client)["plan_requests_used"] == 0
    cost = SHOPS_DOWN.usage.cost_micro_usd
    ledger = store.read("ledger.json")
    assert ledger is not None
    assert ledger.data["spent"]["planning"] == cost
    assert ledger.data["reserved"]["planning"] == 0
    assert ledger.data["running"] == {}
    record = store.read(f"operations/{operation}.json")
    assert record is not None
    assert (record.data["outcome"], record.data["cost"]) == ("shops_unavailable", cost)


def test_a_search_of_unknown_cost_gives_the_request_back_and_keeps_its_reservation(
    tmp_path: Path, store: MemoryStore
) -> None:
    unknown = FindResult(FindOutcome.SHOPS_UNAVAILABLE, cost_known=False)
    app = create_app(settings(tmp_path), store=store, shop_agent=FakeAgent(unknown))
    with TestClient(app) as client:
        join(client)
        assert lines(find(client))[-1]["result"]["counted"] is False
        assert session(client)["plan_requests_used"] == 0
    ledger = store.read("ledger.json")
    assert ledger is not None and ledger.data["spent"]["planning"] == FIND_RESERVE_MICRO_USD


@pytest.mark.parametrize(
    "result",
    [
        FindResult(FindOutcome.NO_MATCH, message="Nothing suits."),
        FindResult(FindOutcome.NO_MATCH, message="Nothing. Some shop pages could not be read."),
        FindResult(FindOutcome.FAILED, message="The shops could not be read right now."),
    ],
)
def test_an_empty_result_or_a_partial_failure_still_counts(
    tmp_path: Path, result: FindResult
) -> None:
    with TestClient(create_app(settings(tmp_path), shop_agent=FakeAgent(result))) as client:
        join(client)
        assert lines(find(client))[-1]["result"]["counted"] is True
        assert session(client)["plan_requests_used"] == 1


def test_a_repeated_operation_after_a_failure_calls_nothing_and_gives_back_once(
    tmp_path: Path,
) -> None:
    agent = FakeAgent(SHOPS_DOWN)
    operation = str(uuid.uuid4())
    with TestClient(create_app(settings(tmp_path), shop_agent=agent)) as client:
        join(client)
        agent.result = FindResult(FindOutcome.NO_MATCH, message="Nothing.")
        lines(find(client))  # so that a second return would show in the count
        assert session(client)["plan_requests_used"] == 1
        agent.result = SHOPS_DOWN
        assert lines(find(client, operation=operation))[-1]["result"]["counted"] is False
        assert session(client)["plan_requests_used"] == 1
        calls = len(agent.requests)
        repeat = find(client, operation=operation)
        assert repeat.status_code == 409
        assert repeat.json()["reason"] == "duplicate"
        assert len(agent.requests) == calls
        assert session(client)["plan_requests_used"] == 1


def test_shops_that_cannot_be_read_never_use_up_a_request(tmp_path: Path) -> None:
    agent = FakeAgent(SHOPS_DOWN)
    with TestClient(create_app(settings(tmp_path), shop_agent=agent)) as client:
        join(client)
        counted = [lines(find(client))[-1]["result"]["counted"] for _ in range(MAX_RECENT_RETURNS)]
        assert counted == [False] * MAX_RECENT_RETURNS
        assert session(client)["plan_requests_used"] == 0

        # Refused instead of charged, before any model call.
        calls = len(agent.requests)
        refused = find(client)
        assert refused.status_code == 503
        assert refused.json()["reason"] == "shops_unavailable"
        assert len(agent.requests) == calls
        assert session(client)["plan_requests_used"] == 0


def test_a_failing_shop_leaves_other_invites_and_other_shops_working(tmp_path: Path) -> None:
    shoes = "https://shop.example/p/schuh-herren-0"
    model = FakeModel(
        call("jacke"), call("schuh"), answer([{"url": shoes, "slot": "footwear", "why": "Closed"}])
    )
    all_routes = {
        **listing("https://down.example", [503] * FAILURES_TO_STOP, word="jacke-herren"),
        **listing(
            "https://shop.example", [page(product("Schuh", offer("50.00")))], word="schuh-herren"
        ),
    }
    down = Shop("Down", "https://down.example", "/p/")
    agent = ShopAgent(model, ShopIndex((down, SHOP), serve(all_routes)))
    with TestClient(create_app(settings(tmp_path), shop_agent=agent)) as client:
        join(client)
        first = lines(find(client))[-1]["result"]
        assert (first["outcome"], first["counted"]) == ("shops_unavailable", False)
        client.cookies.clear()
        join(client, OTHER_CODE)
        second = lines(find(client))[-1]["result"]
        assert second["outcome"] == "success"
        assert [found["url"] for found in second["finds"]] == [shoes]


def test_real_pages_failing_behind_a_readable_sitemap_stop_further_paid_calls(
    tmp_path: Path, store: MemoryStore
) -> None:
    now = [0.0]
    model = FakeModel(call("jacke"))
    all_routes = listing("https://shop.example", [503] * FAILURES_TO_STOP, word="jacke-herren")
    index = ShopIndex((SHOP,), serve(all_routes), clock=lambda: now[0])
    app = create_app(settings(tmp_path), store=store, shop_agent=ShopAgent(model, index))
    operation = str(uuid.uuid4())
    with TestClient(app) as client:
        join(client)
        result = lines(find(client, operation=operation))[-1]["result"]
        assert (result["outcome"], result["counted"]) == ("shops_unavailable", False)
        assert session(client)["plan_requests_used"] == 0
        ledger = store.read("ledger.json")
        assert ledger is not None and ledger.data["spent"]["planning"] > 0

        # While the pages are known to be down, a new search is refused before anything is spent.
        refused = find(client)
        assert refused.status_code == 503
        assert refused.json()["reason"] == "shops_unavailable"
        assert len(model.payloads) == 1

        # Later the same operation is still a duplicate, so it never pays for a second call.
        now[0] += RETRY_FAILED_PAGES_S + 1
        assert find(client, operation=operation).json()["reason"] == "duplicate"
        assert len(model.payloads) == 1
        assert session(client)["plan_requests_used"] == 0


def test_a_failed_settling_leaves_the_request_counted(tmp_path: Path) -> None:
    store = BrokenStore()
    agent = BreakingAgent(store)
    agent.result = SHOPS_DOWN
    with TestClient(create_app(settings(tmp_path), store=store, shop_agent=agent)) as client:
        join(client)
        assert lines(find(client))[-1]["result"]["counted"] is True


def test_every_outcome_of_a_search_has_its_place_in_the_pilot_report() -> None:
    assert {OUTCOME_CATEGORY[outcome.value] for outcome in FindOutcome} == {"succeeded", "failed"}


class BrokenStore(MemoryStore):
    """Fails with an error that is not StoreUnavailable once the search is under way."""

    broken = False

    def replace(self, name: str, data: dict[str, Any], etag: str) -> str:
        if self.broken:
            raise OSError("disk")
        return super().replace(name, data, etag)


class BreakingAgent(FakeAgent):
    def __init__(self, store: BrokenStore) -> None:
        super().__init__()
        self.store = store

    def __call__(self, request: FindRequest, on_step: Callable[[str], None]) -> FindResult:
        self.store.broken = True
        return super().__call__(request, on_step)


def test_the_answer_ends_even_when_settling_fails(tmp_path: Path) -> None:
    store = BrokenStore()
    app = create_app(settings(tmp_path), store=store, shop_agent=BreakingAgent(store))
    with TestClient(app) as client:
        join(client)
        assert lines(find(client))[-1]["result"]["outcome"] == "success"
