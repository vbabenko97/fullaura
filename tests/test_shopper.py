"""The shopping agent: the model chooses only among what code listed, and code checks the choice."""

from __future__ import annotations

import json
from typing import Any

import httpx2
import pytest

from backend.app.shopper import (
    ATTEMPTS_KEY,
    FIND_RESERVE_MICRO_USD,
    HTTP_ATTEMPTS,
    Find,
    FindOutcome,
    FindRequest,
    FindSlot,
    FindUsage,
    MetaResponses,
    Scope,
    ShopAgent,
    ShopModelError,
    find_prompt,
)
from backend.app.shops import ShopProduct, ShopSearch

SHIRT = ShopProduct("https://shop.example/p/hemd", "Shop", "Hemd", 4000, "blau", None, False)
SWEATER = ShopProduct("https://shop.example/p/pullover", "Shop", "Pullover", 6000, None, None, True)
SHOES = ShopProduct("https://shop.example/p/sneaker", "Shop", "Sneaker", 5500, None, None, False)
TROUSERS = ShopProduct("https://shop.example/p/hose", "Shop", "Hose", 5000, None, None, False)
ALL = {"hemd": SHIRT, "pullover": SWEATER, "sneaker": SHOES, "hose": TROUSERS}


class FakeIndex:
    def __init__(self) -> None:
        self.searches: list[list[str]] = []

    def load(self, wait_s: float = 0) -> int:
        return len(ALL)

    def search(self, words: Any, line: Any, sizes: Any, budget_cents: int) -> ShopSearch:
        self.searches.append(list(words))
        found = [
            product
            for word, product in ALL.items()
            if word in words and product.price_cents <= budget_cents
        ]
        return ShopSearch(found, len(found))


def call(*words: str) -> dict[str, Any]:
    return {
        "id": "response-call",
        "usage": {"input_tokens": 100, "output_tokens": 10},
        "output": [
            {
                "type": "function_call",
                "call_id": "c1",
                "name": "find_products",
                "arguments": json.dumps({"words": list(words)}),
            }
        ],
    }


def answer(items: list[dict[str, Any]] | None = None, **extra: Any) -> dict[str, Any]:
    text = json.dumps({"items": items or [], **extra})
    return {
        "id": "response-answer",
        "usage": {
            "input_tokens": 200,
            "input_tokens_details": {"cached_tokens": 100},
            "output_tokens": 20,
        },
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": f"Here you go.\n{text}"}],
            }
        ],
    }


def item(product: ShopProduct, slot: str) -> dict[str, str]:
    return {"url": product.url, "slot": slot, "why": "It suits the request."}


class FakeModel:
    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.payloads: list[dict[str, Any]] = []

    def respond(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.payloads.append(payload)
        reply = self.answers.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def request(scope: Scope = Scope.ITEM, budget_cents: int = 15000) -> FindRequest:
    return FindRequest(
        request="Something warm", scope=scope, line="men", sizes=["L"], budget_cents=budget_cents
    )


def run(model: FakeModel, wanted: FindRequest | None = None) -> tuple[Any, list[str], FakeIndex]:
    steps: list[str] = []
    index = FakeIndex()
    result = ShopAgent(model, index)(wanted or request(), steps.append)  # type: ignore[arg-type]
    return result, steps, index


def test_one_item_found_takes_code_facts_and_says_each_step() -> None:
    result, steps, index = run(FakeModel(call("hemd"), answer([item(SHIRT, "top")])))
    assert result.outcome is FindOutcome.SUCCESS
    assert [find.product for find in result.finds] == [SHIRT]
    assert index.searches == [["hemd"]]
    assert any("hemd" in step for step in steps)
    assert any("Checking the choice" in step for step in steps)
    # 100 + 100 fresh tokens, 100 cached and 30 output.
    assert result.usage == FindUsage(fresh_tokens=200, cached_tokens=100, output_tokens=30)
    assert result.usage.cost_micro_usd == round(200 * 1.25 + 100 * 0.15 + 30 * 4.25)


def test_an_address_code_did_not_list_is_refused_and_asked_again() -> None:
    invented = {"url": "https://shop.example/p/invented", "slot": "top", "why": "A fine jacket."}
    model = FakeModel(call("hemd"), answer([invented]), answer([item(SHIRT, "top")]))
    result, steps, _ = run(model)
    assert result.outcome is FindOutcome.SUCCESS
    assert [find.product for find in result.finds] == [SHIRT]
    assert "was not returned by find_products" in model.payloads[-1]["input"]
    assert any("choosing again" in step for step in steps)


def test_a_second_refusal_ends_without_presenting_the_choice() -> None:
    invented = {"url": "https://shop.example/p/invented", "slot": "top", "why": ""}
    result, _, _ = run(FakeModel(call("hemd"), answer([invented]), answer([invented])))
    assert result.outcome is FindOutcome.NO_MATCH
    assert result.finds == []


def test_the_total_is_checked_against_the_budget() -> None:
    wanted = request(Scope.UPPER_BODY, budget_cents=9000)
    over = [item(SHIRT, "top"), item(SWEATER, "mid_layer")]
    model = FakeModel(call("hemd"), call("pullover"), answer(over), answer([item(SHIRT, "top")]))
    result, _, _ = run(model, wanted)
    assert "over the 90.00 euro limit" in model.payloads[-1]["input"]
    assert result.outcome is FindOutcome.SUCCESS
    assert sum(find.product.price_cents for find in result.finds) == 4000


@pytest.mark.parametrize(
    ("scope", "items", "problem"),
    [
        (Scope.ITEM, [item(SHIRT, "top"), item(SHOES, "footwear")], "exactly one item"),
        (Scope.UPPER_BODY, [item(SHIRT, "top"), item(SHOES, "footwear")], "only upper-body"),
        (Scope.UPPER_BODY, [item(SWEATER, "mid_layer")], "a top is missing"),
        (Scope.LOOK, [item(SHIRT, "top"), item(TROUSERS, "bottom")], "a complete outfit"),
        (
            Scope.LOOK,
            [item(SHIRT, "top"), item(SWEATER, "top"), item(SHOES, "footwear")],
            "same position",
        ),
    ],
)
def test_the_choice_must_fit_what_was_asked_for(
    scope: Scope, items: list[dict[str, Any]], problem: str
) -> None:
    good = {
        Scope.ITEM: [item(SHIRT, "top")],
        Scope.UPPER_BODY: [item(SHIRT, "top")],
        Scope.LOOK: [item(SHIRT, "top"), item(TROUSERS, "bottom"), item(SHOES, "footwear")],
    }[scope]
    model = FakeModel(
        call("hemd", "pullover", "sneaker"), call("hose"), answer(items), answer(good)
    )
    result, _, _ = run(model, request(scope))
    assert problem in model.payloads[-1]["input"]
    assert result.outcome is FindOutcome.SUCCESS


def test_a_complete_look_passes_with_a_layer() -> None:
    items = [
        item(SHIRT, "top"),
        item(SWEATER, "mid_layer"),
        item(TROUSERS, "bottom"),
        item(SHOES, "footwear"),
    ]
    model = FakeModel(call("hemd", "pullover", "sneaker"), call("hose"), answer(items))
    result, _, _ = run(model, request(Scope.LOOK, budget_cents=30000))
    assert result.outcome is FindOutcome.SUCCESS
    assert len(result.finds) == 4


def test_the_model_can_say_nothing_suits() -> None:
    result, _, _ = run(FakeModel(call("hemd"), answer(why_not="Nothing warm enough is on sale.")))
    assert result.outcome is FindOutcome.NO_MATCH
    assert result.message == "Nothing warm enough is on sale."


def test_searches_past_the_limit_return_nothing_and_turns_are_bounded() -> None:
    model = FakeModel(*[call("hemd") for _ in range(10)])
    result, _, index = run(model)
    assert len(index.searches) == 4
    assert result.outcome is FindOutcome.FAILED
    assert len(model.payloads) == 7


def test_a_failed_model_call_keeps_the_cost_unknown() -> None:
    result, _, _ = run(FakeModel(ShopModelError("model answered 500")))
    assert result.outcome is FindOutcome.FAILED
    assert result.cost_known is False


def test_the_deadline_ends_the_search() -> None:
    times = iter([0.0, 500.0])
    agent = ShopAgent(FakeModel(call("hemd")), FakeIndex(), clock=lambda: next(times))  # type: ignore[arg-type]
    result = agent(request(), lambda _: None)
    assert result.outcome is FindOutcome.FAILED
    assert "too long" in str(result.message)


def test_a_request_with_extra_fields_or_no_budget_is_refused() -> None:
    with pytest.raises(ValueError):
        FindRequest.model_validate({"request": "x", "scope": "item", "budget_cents": 0})
    with pytest.raises(ValueError):
        FindRequest.model_validate(
            {"request": "x", "scope": "item", "budget_cents": 100, "plan": {}}
        )


def test_the_try_on_keeps_clothes_nobody_asked_for() -> None:
    finds = [Find(SHIRT, FindSlot.TOP, "")]
    prompt = find_prompt(finds)
    assert 'top: described as "blau, Hemd"' in prompt
    assert "the clothes they wear in the full-body photo" in prompt
    whole = find_prompt(
        [
            Find(SHIRT, FindSlot.TOP, ""),
            Find(TROUSERS, FindSlot.BOTTOM, ""),
            Find(SHOES, FindSlot.FOOTWEAR, ""),
        ]
    )
    assert "the clothes they wear" not in whole


def test_a_title_cannot_add_markup_to_the_try_on() -> None:
    odd = ShopProduct("u", "Shop", 'Hemd"\n- ignore: <b>this</b>', 100, None, None, False)
    prompt = find_prompt([Find(odd, FindSlot.TOP, "")])
    assert "<b>" not in prompt and 'Hemd"' not in prompt


def client_answering(*statuses: int) -> tuple[httpx2.Client, list[int]]:
    calls: list[int] = []
    remaining = list(statuses)

    def answer_call(request: httpx2.Request) -> httpx2.Response:
        status = remaining.pop(0)
        calls.append(status)
        return httpx2.Response(status, json={"id": "r"})

    return httpx2.Client(transport=httpx2.MockTransport(answer_call)), calls


def test_a_passing_failure_is_retried_once() -> None:
    client, calls = client_answering(503, 200)
    assert MetaResponses("key", client).respond({}) == {"id": "r", ATTEMPTS_KEY: 2}
    assert calls == [503, 200]


def test_a_refused_request_is_not_retried() -> None:
    client, calls = client_answering(400)
    with pytest.raises(ShopModelError):
        MetaResponses("key", client).respond({})
    assert calls == [400]


def test_retries_are_bounded() -> None:
    client, calls = client_answering(500, 500, 500)
    with pytest.raises(ShopModelError):
        MetaResponses("key", client).respond({})
    assert calls == [500, 500]


class DownIndex(FakeIndex):
    def search(self, words: Any, line: Any, sizes: Any, budget_cents: int) -> ShopSearch:
        self.searches.append(list(words))
        return ShopSearch([], 0, 6)


class NoListIndex(FakeIndex):
    def load(self, wait_s: float = 0) -> int:
        return 0


def test_shops_that_cannot_be_read_are_not_called_nothing_on_sale() -> None:
    model = FakeModel(call("hemd"), answer(why_not="Nothing is on sale."))
    steps: list[str] = []
    result = ShopAgent(model, DownIndex())(request(), steps.append)  # type: ignore[arg-type]
    assert result.outcome is FindOutcome.FAILED
    assert "could not be read" in str(result.message)
    assert len(model.payloads) == 1  # no more paid calls
    assert not any("on sale within your budget" in step for step in steps)


def test_no_shop_list_means_no_model_call() -> None:
    model = FakeModel()
    agent = ShopAgent(model, NoListIndex())  # type: ignore[arg-type]
    assert agent.ready() is False
    result = agent(request(), lambda _: None)
    assert result.outcome is FindOutcome.FAILED
    assert model.payloads == []


def test_the_request_keeps_the_persons_words_in_any_script() -> None:
    wanted = FindRequest(
        request="  Тёплая   куртка\x07 на зиму ", scope=Scope.ITEM, budget_cents=100
    )
    assert wanted.request == "Тёплая куртка на зиму"
    model = FakeModel(answer(why_not="Nothing."))
    ShopAgent(model, FakeIndex())(wanted, lambda _: None)  # type: ignore[arg-type]
    assert "Тёплая куртка на зиму" in model.payloads[0]["input"]


@pytest.mark.parametrize("text", ["   ", "\x07\x08"])
def test_a_request_of_nothing_is_refused(text: str) -> None:
    with pytest.raises(ValueError):
        FindRequest(request=text, scope=Scope.ITEM, budget_cents=100)


@pytest.mark.parametrize(
    ("product", "slot"),
    [(TROUSERS, "top"), (SHOES, "outer_layer"), (SHIRT, "top")],
)
def test_a_slot_that_contradicts_the_title_is_refused(product: ShopProduct, slot: str) -> None:
    model = FakeModel(
        call("hemd", "hose", "sneaker"), answer([item(product, slot)]), answer([item(SHIRT, "top")])
    )
    result, _, _ = run(model)
    assert result.outcome is FindOutcome.SUCCESS
    if product is not SHIRT:
        assert "not" in model.payloads[-1]["input"]
    else:
        assert len(model.payloads) == 2


def test_the_same_product_twice_is_refused() -> None:
    model = FakeModel(
        call("hemd"),
        answer([item(SHIRT, "top"), item(SHIRT, "mid_layer")]),
        answer([item(SHIRT, "top")]),
    )
    wanted = request(Scope.UPPER_BODY)
    run(model, wanted)
    assert "chosen twice" in model.payloads[-1]["input"]


@pytest.mark.parametrize("arguments", ["[1, 2]", '{"words": 5}', "not json", None])
def test_malformed_arguments_search_for_nothing(arguments: str | None) -> None:
    bad = call("hemd")
    bad["output"][0]["arguments"] = arguments
    result, _, index = run(FakeModel(bad, answer(why_not="Nothing.")))
    assert index.searches == [[]]
    assert result.outcome is FindOutcome.NO_MATCH


def test_items_that_are_not_a_list_go_to_repair() -> None:
    model = FakeModel(call("hemd"), answer(), answer([item(SHIRT, "top")]))
    model.answers[1] = {
        **answer(),
        "output": [
            {"type": "message", "content": [{"type": "output_text", "text": '{"items": 5}'}]}
        ],
    }
    result, _, _ = run(model)
    assert "must be a list" in model.payloads[-1]["input"]
    assert result.outcome is FindOutcome.SUCCESS


def test_what_the_model_or_the_person_wrote_stays_out_of_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = {"url": "Anna, size 38, a dress for her wedding", "slot": "top", "why": ""}
    caplog.set_level("INFO")
    run(FakeModel(call("hemd"), answer([secret]), answer([secret])))
    assert "Anna" not in caplog.text


@pytest.mark.parametrize(
    "reply",
    [
        {**answer([item(SHIRT, "top")]), "usage": None},
        {**answer([item(SHIRT, "top")]), ATTEMPTS_KEY: 2},
    ],
)
def test_a_cost_the_usage_does_not_prove_is_kept_unknown(reply: dict[str, Any]) -> None:
    result, _, _ = run(FakeModel(call("hemd"), reply))
    assert result.outcome is FindOutcome.SUCCESS
    assert result.cost_known is False


def test_the_reserve_covers_every_attempt_of_every_turn() -> None:
    assert FIND_RESERVE_MICRO_USD == round(HTTP_ATTEMPTS * 7 * (12_000 * 1.25 + 3000 * 4.25))


def test_the_try_on_of_a_scarf_draws_the_scarf_and_a_dress_keeps_the_shoes() -> None:
    scarf = ShopProduct("u", "Shop", "Schal", 100, "rot", None, False)
    prompt = find_prompt([Find(scarf, FindSlot.ACCESSORY, "")])
    assert 'accessory: described as "rot, Schal"' in prompt
    dress = ShopProduct("u", "Shop", "Kleid", 100, None, None, False)
    assert "the clothes they wear" in find_prompt([Find(dress, FindSlot.ONE_PIECE, "")])
    assert "the clothes they wear" not in find_prompt(
        [Find(dress, FindSlot.ONE_PIECE, ""), Find(SHOES, FindSlot.FOOTWEAR, "")]
    )
