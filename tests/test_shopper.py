"""The shopping agent: the model chooses only among what code listed, and code checks the choice."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx2
import pytest

from backend.app.catalog import Catalog, Category
from backend.app.shopper import (
    ATTEMPTS_KEY,
    FIND_RESERVE_MICRO_USD,
    HTTP_ATTEMPTS,
    MAX_OUTPUT_TOKENS,
    Find,
    FindOutcome,
    FindRequest,
    FindSlot,
    FindUsage,
    MetaResponses,
    Scope,
    ShopAgent,
    ShopModelError,
    _slot_problem,
    find_json,
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

    def pages_down(self) -> bool:
        return False

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


def test_a_passing_failure_is_retried_once_and_says_what_failed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("WARNING")
    client, calls = client_answering(503, 200)
    assert MetaResponses("key", client).respond({}) == {"id": "r", ATTEMPTS_KEY: 2}
    assert calls == [503, 200]
    assert "shop model attempt 1 failed: status 503" in caplog.text


def test_a_lost_connection_is_retried_once_and_says_what_failed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("WARNING")
    attempts = []

    def answer_call(request: httpx2.Request) -> httpx2.Response:
        attempts.append(request)
        if len(attempts) == 1:
            raise httpx2.ConnectError("secret detail of the connection")
        return httpx2.Response(200, json={"id": "r"})

    client = httpx2.Client(transport=httpx2.MockTransport(answer_call))
    assert MetaResponses("key", client).respond({}) == {"id": "r", ATTEMPTS_KEY: 2}
    assert "shop model attempt 1 failed: ConnectError" in caplog.text
    assert "secret detail" not in caplog.text


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
    assert result.outcome is FindOutcome.SHOPS_UNAVAILABLE
    assert "could not be read" in str(result.message)
    assert len(model.payloads) == 1  # no more paid calls
    assert result.usage.fresh_tokens == 100  # the call that was made is still its cost
    assert not any("on sale within your budget" in step for step in steps)


def test_no_shop_list_means_no_model_call() -> None:
    model = FakeModel()
    agent = ShopAgent(model, NoListIndex())  # type: ignore[arg-type]
    assert agent.ready() is False
    result = agent(request(), lambda _: None)
    assert result.outcome is FindOutcome.SHOPS_UNAVAILABLE
    assert model.payloads == []


class PagesDownIndex(FakeIndex):
    def pages_down(self) -> bool:
        return True


def test_shops_whose_pages_just_failed_are_not_ready() -> None:
    agent = ShopAgent(FakeModel(), PagesDownIndex())  # type: ignore[arg-type]
    assert agent.ready() is False


class ScriptedIndex(FakeIndex):
    def __init__(self, *searches: ShopSearch) -> None:
        super().__init__()
        self.answers = list(searches)

    def search(self, words: Any, line: Any, sizes: Any, budget_cents: int) -> ShopSearch:
        return self.answers.pop(0)


def run_on(model: FakeModel, index: FakeIndex) -> tuple[Any, list[str]]:
    steps: list[str] = []
    return ShopAgent(model, index)(request(), steps.append), steps  # type: ignore[arg-type]


def test_nothing_on_sale_is_not_a_shop_failure() -> None:
    model = FakeModel(call("hemd"), answer(why_not="Nothing suits."))
    result, _ = run_on(model, ScriptedIndex(ShopSearch([], 6, 0)))
    assert result.outcome is FindOutcome.NO_MATCH
    assert result.message == "Nothing suits."


def test_some_pages_failing_is_not_a_shop_failure_and_is_said() -> None:
    model = FakeModel(call("hemd"), answer(why_not="Nothing suits."))
    result, steps = run_on(model, ScriptedIndex(ShopSearch([], 2, 4)))
    assert result.outcome is FindOutcome.NO_MATCH
    assert result.message == "Nothing suits. Some shop pages could not be read."
    assert (
        "Read the shops' pages: 0 on sale within your budget; some pages could not be read" in steps
    )


def test_a_product_found_while_some_pages_fail_is_still_chosen() -> None:
    model = FakeModel(call("hemd"), answer([item(SHIRT, "top")]))
    result, _ = run_on(model, ScriptedIndex(ShopSearch([SHIRT], 1, 5)))
    assert result.outcome is FindOutcome.SUCCESS
    assert result.message is None


def test_pages_read_earlier_mean_the_shops_were_not_wholly_unavailable() -> None:
    model = FakeModel(call("hemd"), call("hose"))
    result, _ = run_on(model, ScriptedIndex(ShopSearch([], 3, 0), ShopSearch([], 0, 3)))
    assert result.outcome is FindOutcome.FAILED
    assert "could not be read" in str(result.message)


def test_a_failed_search_after_products_were_shown_leaves_the_choice_open() -> None:
    model = FakeModel(call("hemd"), call("hose"), answer([item(SHIRT, "top")]))
    result, _ = run_on(model, ScriptedIndex(ShopSearch([SHIRT], 1, 0), ShopSearch([], 0, 3)))
    assert result.outcome is FindOutcome.SUCCESS


def titled(title: str) -> ShopProduct:
    return ShopProduct(f"https://shop.example/p/{title}", "Shop", title, 1000, None, None, False)


class TitledIndex(FakeIndex):
    def __init__(self, *products: ShopProduct) -> None:
        super().__init__()
        self.products = list(products)

    def search(self, words: Any, line: Any, sizes: Any, budget_cents: int) -> ShopSearch:
        return ShopSearch(self.products, len(self.products))


def choose(scope: Scope, *chosen: tuple[str, str]) -> tuple[Any, FakeModel]:
    """Offer these titles and have the model choose them in these slots, then give up."""
    products = {title: titled(title) for title, _ in chosen}
    picks = [item(products[title], slot) for title, slot in chosen]
    model = FakeModel(call("x"), answer(picks), answer(why_not="Given up."))
    index = TitledIndex(*products.values())
    return ShopAgent(model, index)(request(scope), lambda _: None), model  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("title", "slot"),
    [
        ("CORD-MINI-KLEID MIT RUNDHALS", "top"),
        ("Hemdblusenkleid in blau von Tom Tailor", "top"),
        ("Kleid, rot", "top"),
        ("Kleid", "mid_layer"),
        ("Winterjacke", "top"),
        ("Strickjacke", "bottom"),
        ("Parka", "footwear"),
        ("Basic T-Shirt mit Rundhalsausschnitt", "outer_layer"),
        ("Hemd", "footwear"),
        ("Strickpullover", "bottom"),
        ("Relaxed Chino Hose in schwarz von Tom Tailor", "top"),
        ("LENOVAA SATIN-ROCK", "one_piece"),
        ("Tom Tailor Sneaker", "accessory"),
        ("Schneestiefeletten mit Stickerei", "top"),
        ("Handschuhe aus Leder", "footwear"),
        ("Sneakersocken", "footwear"),
        ("Schal", "top"),
    ],
)
def test_a_slot_the_title_rules_out_is_refused(title: str, slot: str) -> None:
    result, model = choose(Scope.ITEM, (title, slot))
    assert ", not " in model.payloads[-1]["input"]
    assert result.outcome is FindOutcome.NO_MATCH


@pytest.mark.parametrize(
    ("title", "slot"),
    [
        ("CORD-MINI-KLEID MIT RUNDHALS", "one_piece"),
        ("Hemd (blau)", "top"),
        ("Jeanshemd", "top"),
        ("Jeansjacke", "outer_layer"),
        ("Hose mit Gürtel", "bottom"),
        ("T-Shirt mit Rock-Print", "top"),
        ("Kleid mit Gürtel", "one_piece"),
        ("Handschuhe aus Leder", "accessory"),
        ("Sneakersocken", "accessory"),
        ("Pullover", "top"),
        ("Pullover", "mid_layer"),
        ("Strickjacke", "mid_layer"),
        ("Strickjacke", "outer_layer"),
        ("SELVEDGE JEANS-OVERSHIRT", "top"),
        ("SELVEDGE JEANS-OVERSHIRT", "outer_layer"),
        ("Merino Runners MEN", "footwear"),
    ],
)
def test_a_slot_the_title_allows_is_confirmed(title: str, slot: str) -> None:
    result, _ = choose(Scope.ITEM, (title, slot))
    assert result.outcome is FindOutcome.SUCCESS
    assert result.finds[0].slot_checked is True


@pytest.mark.parametrize(("title", "slot"), [("Beach", "footwear"), ("Kommod Classic", "top")])
def test_a_title_that_names_no_kind_is_shown_as_not_confirmed(title: str, slot: str) -> None:
    result, _ = choose(Scope.ITEM, (title, slot))
    assert result.outcome is FindOutcome.SUCCESS
    assert result.finds[0].slot_checked is False
    wanted = request(Scope.ITEM)
    assert find_json(result, wanted, None)["finds"][0]["slot_checked"] is False


def test_a_title_that_names_two_kinds_cannot_fill_one_position() -> None:
    result, model = choose(Scope.ITEM, ("Bluse und Rock", "top"))
    assert "more than one kind" in model.payloads[-1]["input"]
    assert result.outcome is FindOutcome.NO_MATCH


def test_two_things_of_one_kind_can_fill_one_position() -> None:
    result, _ = choose(Scope.ITEM, ("Mütze und Schal", "accessory"))
    assert result.outcome is FindOutcome.SUCCESS
    assert result.finds[0].slot_checked is True


@pytest.mark.parametrize(
    ("scope", "chosen"),
    [
        (Scope.UPPER_BODY, [("T-Shirt", "top")]),
        (Scope.UPPER_BODY, [("Pullover", "top"), ("Winterjacke", "outer_layer")]),
        (
            Scope.UPPER_BODY,
            [("Hemd", "top"), ("Pullover", "mid_layer"), ("Parka", "outer_layer")],
        ),
        (Scope.ITEM, [("Sneaker", "footwear")]),
        (Scope.ITEM, [("Kleid", "one_piece")]),
        (Scope.LOOK, [("T-Shirt", "top"), ("Jeans", "bottom"), ("Sneaker", "footwear")]),
        (Scope.LOOK, [("Kleid", "one_piece"), ("Boots", "footwear")]),
        (
            Scope.LOOK,
            [("Hemd", "top"), ("Hose", "bottom"), ("Schuhe", "footwear"), ("Parka", "outer_layer")],
        ),
    ],
)
def test_each_scope_accepts_what_it_asks_for(scope: Scope, chosen: list[tuple[str, str]]) -> None:
    result, _ = choose(scope, *chosen)
    assert result.outcome is FindOutcome.SUCCESS
    assert all(find.slot_checked for find in result.finds)


@pytest.mark.parametrize(
    ("scope", "chosen", "problem"),
    [
        (Scope.UPPER_BODY, [("Kleid", "top")], "is a one-piece, not top"),
        (Scope.UPPER_BODY, [("Kleid", "one_piece")], "only upper-body"),
        (Scope.UPPER_BODY, [("Pullover", "mid_layer")], "a top is missing"),
        (Scope.UPPER_BODY, [("T-Shirt", "top"), ("Hose", "bottom")], "only upper-body"),
        (Scope.ITEM, [("T-Shirt", "top"), ("Hose", "bottom")], "exactly one"),
        (Scope.LOOK, [("Kleid", "top"), ("Hose", "bottom"), ("Boots", "footwear")], "one-piece"),
        (Scope.LOOK, [("T-Shirt", "top"), ("Hose", "bottom")], "a complete outfit"),
        (
            Scope.LOOK,
            [("Kleid", "one_piece"), ("Hose", "bottom"), ("Boots", "footwear")],
            "a one-piece replaces",
        ),
    ],
)
def test_each_scope_refuses_what_it_does_not_ask_for(
    scope: Scope, chosen: list[tuple[str, str]], problem: str
) -> None:
    result, model = choose(scope, *chosen)
    assert problem in model.payloads[-1]["input"]
    assert result.outcome is FindOutcome.NO_MATCH


def test_no_catalog_title_is_refused_for_its_own_category() -> None:
    """The real titles of the shops this mode reads, with the position their record gives them."""
    positions = {
        Category.TOP: FindSlot.TOP,
        Category.KNITWEAR: FindSlot.MID_LAYER,
        Category.OUTERWEAR: FindSlot.OUTER_LAYER,
        Category.BOTTOM: FindSlot.BOTTOM,
        Category.ONE_PIECE: FindSlot.ONE_PIECE,
        Category.FOOTWEAR: FindSlot.FOOTWEAR,
    }
    catalog = Catalog.model_validate_json(Path("backend/data/catalog.json").read_text("utf-8"))
    unconfirmed = set()
    for record in catalog.products:
        slot = positions[record.category]
        assert _slot_problem(record.title, slot) is None, record.title
        if not Find(titled(record.title), slot, "").slot_checked:
            unconfirmed.add(record.title)
    # Shoes whose names state no kind, so the page says that the position is not confirmed.
    assert unconfirmed == {"Beach", "Kommod Classic", "Tramper Flex"}


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
    ("reply", "reason"),
    [
        ({**answer([item(SHIRT, "top")]), "usage": None}, "the answer reported no usage"),
        ({**answer([item(SHIRT, "top")]), ATTEMPTS_KEY: 2}, "it took 2 HTTP attempts"),
    ],
)
def test_a_cost_the_usage_does_not_prove_is_kept_unknown_and_says_why(
    reply: dict[str, Any], reason: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("WARNING")
    result, _, _ = run(FakeModel(call("hemd"), reply))
    assert result.outcome is FindOutcome.SUCCESS
    assert result.cost_known is False
    assert f"shop call cost unknown: {reason}" in caplog.text
    # Nothing the person or the model wrote goes with it.
    assert "Something warm" not in caplog.text and "suits the request" not in caplog.text


def test_a_cost_the_usage_proves_logs_no_reason(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("WARNING")
    result, _, _ = run(FakeModel(call("hemd"), answer([item(SHIRT, "top")])))
    assert result.cost_known is True
    assert "cost unknown" not in caplog.text


def test_the_reserve_covers_every_attempt_of_every_turn() -> None:
    reserve = HTTP_ATTEMPTS * 7 * (12_000 * 1.25 + MAX_OUTPUT_TOKENS * 4.25)
    assert FIND_RESERVE_MICRO_USD == round(reserve)


def test_the_model_is_asked_to_keep_its_reasoning_within_the_output_cap() -> None:
    model = FakeModel(answer(why_not="Nothing."))
    run(model)
    assert model.payloads[0]["reasoning"] == {"effort": "medium"}
    assert model.payloads[0]["max_output_tokens"] == MAX_OUTPUT_TOKENS


# What Meta's API answered on 2026-11-02 when reasoning used up the output cap.
CUT_OFF = {
    "id": "response-cut-off",
    "status": "incomplete",
    "incomplete_details": {"reason": "max_output_tokens"},
    "usage": {"input_tokens": 5000, "output_tokens": 1200},
    "output": [{"type": "reasoning", "summary": []}],
}


def test_an_answer_cut_off_by_the_output_cap_is_asked_for_again_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("WARNING")
    model = FakeModel(call("hemd"), CUT_OFF, answer([item(SHIRT, "top")]))
    result, _, _ = run(model)
    assert result.outcome is FindOutcome.SUCCESS
    assert "held no JSON object" in model.payloads[-1]["input"]
    assert "shop model answer cut off: max output tokens" in caplog.text
    assert result.usage.output_tokens == 1200 + 10 + 20  # a cut-off answer is paid for too


def test_two_answers_cut_off_end_the_search_and_are_not_called_nothing_on_sale() -> None:
    result, _, _ = run(FakeModel(call("hemd"), CUT_OFF, CUT_OFF))
    assert result.outcome is FindOutcome.NO_MATCH
    assert result.finds == []
    assert "No choice passed the checks" in str(result.message)


def test_the_try_on_of_a_scarf_draws_the_scarf_and_a_dress_keeps_the_shoes() -> None:
    scarf = ShopProduct("u", "Shop", "Schal", 100, "rot", None, False)
    prompt = find_prompt([Find(scarf, FindSlot.ACCESSORY, "")])
    assert 'accessory: described as "rot, Schal"' in prompt
    dress = ShopProduct("u", "Shop", "Kleid", 100, None, None, False)
    assert "the clothes they wear" in find_prompt([Find(dress, FindSlot.ONE_PIECE, "")])
    assert "the clothes they wear" not in find_prompt(
        [Find(dress, FindSlot.ONE_PIECE, ""), Find(SHOES, FindSlot.FOOTWEAR, "")]
    )


@pytest.mark.parametrize(
    "usage",
    [
        {"input_tokens": -100000, "output_tokens": 100},
        {"input_tokens": 2, "output_tokens": -100000},
        {"input_tokens": 5, "input_tokens_details": {"cached_tokens": 7}, "output_tokens": 10},
        {"input_tokens": "100", "output_tokens": 10},
        {"input_tokens": 100},
        {"input_tokens": 100, "output_tokens": 10, "input_tokens_details": []},
    ],
)
def test_invalid_model_usage_never_reduces_spending(usage: dict[str, Any]) -> None:
    reply = {**answer([item(SHIRT, "top")]), "usage": usage}
    result, _, _ = run(FakeModel(call("hemd"), reply))
    assert result.outcome is FindOutcome.SUCCESS
    assert result.cost_known is False
    assert result.usage.cost_micro_usd >= 0


def said(text: str) -> dict[str, Any]:
    message = {"type": "message", "content": [{"type": "output_text", "text": text}]}
    return {**answer(), "output": [message]}


DRAFT = json.dumps({"items": [item(SHIRT, "top")]})
CHOSEN = json.dumps({"items": [item(SWEATER, "mid_layer")]})


def test_the_answers_fields_can_come_in_any_order() -> None:
    reply = said('{"why_not": "Nothing warm enough is on sale.", "items": []}')
    result, _, _ = run(FakeModel(call("hemd"), reply, answer(why_not="Asked again.")))
    assert result.outcome is FindOutcome.NO_MATCH
    assert result.message == "Nothing warm enough is on sale."


@pytest.mark.parametrize(
    "text",
    [
        f'{CHOSEN}\n{{"unrelated": true}}',
        f"With {{your size}} in mind: {CHOSEN}",
        f"First idea: {DRAFT}\nBetter: {CHOSEN}",
    ],
    ids=["another object after", "a stray brace before", "a draft before"],
)
def test_the_last_object_with_items_is_the_answer(text: str) -> None:
    model = FakeModel(call("hemd", "pullover"), said(text), answer(why_not="Asked again."))
    result, _, _ = run(model)
    assert [find.product for find in result.finds] == [SWEATER]


def test_a_broken_object_after_a_draft_is_asked_again_rather_than_taking_the_draft() -> None:
    broken = '{"items": [{"url": "https://shop.example/p/pullover", "slot": "mid_layer"},]}'
    model = FakeModel(
        call("hemd", "pullover"),
        said(f"{DRAFT}\nBetter: {broken}"),
        answer([item(SWEATER, "mid_layer")]),
    )
    result, _, _ = run(model)
    assert "held no JSON object" in model.payloads[-1]["input"]
    assert [find.product for find in result.finds] == [SWEATER]
