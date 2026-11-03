import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PrivacyNotice } from "../src/PrivacyNotice";
import { ShopMode } from "../src/ShopMode";
import type { Session } from "../src/types";

const SESSION: Session = {
  beta: true,
  invited: true,
  accounting: true,
  age_confirmed: true,
  photo_consent: false,
  plan_requests_used: 0,
  plan_requests_limit: 8,
  preview_requests_used: 0,
  preview_requests_limit: 12,
  planning_open: true,
  previews_open: true,
  pilot_ends_at: null,
  pilot_ended: false,
  records_purge_at: null,
  feedback_ids: [],
  has_plan: false,
  design_open: false,
  find_open: true,
  invite_scope: null,
};
const FIND = {
  url: "https://shop.example/p/jacke",
  shop: "Shop",
  title: "Winterjacke",
  color: "schwarz",
  material: "100% Polyester",
  price_cents: 9000,
  size_checked: false,
  slot: "outer_layer",
  slot_checked: true,
  why: "Warm enough",
};
const RESULT = {
  outcome: "success",
  message: null,
  find_token: "token-1",
  scope: "item",
  budget_cents: 15000,
  total_cents: 9000,
  counted: true,
  finds: [FIND],
};

function stream(...lines: unknown[]): Response {
  return new Response(lines.map((line) => `${JSON.stringify(line)}\n`).join(""), { status: 200 });
}

function fill(budget = "150") {
  fireEvent.change(screen.getByLabelText("What do you need?"), { target: { value: "A warm jacket" } });
  fireEvent.change(screen.getByLabelText("Spend up to (EUR)"), { target: { value: budget } });
}

function show(session: Session = SESSION, closedText: string | null = null) {
  const onFinished = vi.fn();
  render(
    <ShopMode closedText={closedText} onConsent={async () => null} onFinished={onFinished} remaining={8} session={session}>
      <p>Feedback here</p>
    </ShopMode>,
  );
  return onFinished;
}

const SEARCH_KEY = "fullaura.last-search.v1";
const FORM = { request: "A warm jacket", scope: "item", line: "men", sizes: "L", budget: "150" };

function remember(search: Record<string, unknown>) {
  sessionStorage.setItem(SEARCH_KEY, JSON.stringify({ invite_scope: null, form: FORM, ...search }));
}

describe("the shopping agent's page", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.useRealTimers();
    sessionStorage.clear();
  });

  it("brings the last search back after a reload, with its form and try-on, at no cost", async () => {
    const fetchMock = vi.fn(async () => stream({ result: RESULT }));
    vi.stubGlobal("fetch", fetchMock);
    show();
    fill();
    fireEvent.click(screen.getByRole("radio", { name: "One item" }));
    fireEvent.click(screen.getByRole("radio", { name: "Men's" }));
    fireEvent.change(screen.getByLabelText("Your sizes (optional)"), { target: { value: "L" } });
    fireEvent.click(screen.getByRole("button", { name: "Find it" }));
    await screen.findByRole("heading", { name: "Winterjacke" });
    cleanup();
    fetchMock.mockClear();

    show();
    expect(screen.getByRole("heading", { name: "Winterjacke" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Try a look on" })).toBeTruthy();
    expect((screen.getByLabelText("What do you need?") as HTMLTextAreaElement).value).toBe("A warm jacket");
    expect((screen.getByLabelText("Spend up to (EUR)") as HTMLInputElement).value).toBe("150");
    expect((screen.getByLabelText("Your sizes (optional)") as HTMLInputElement).value).toBe("L");
    expect((screen.getByRole("radio", { name: "One item" }) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByRole("radio", { name: "Men's" }) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByRole("button", { name: "Find it" }) as HTMLButtonElement).disabled).toBe(false);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("waits for a search that a server still waking does not know yet", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    remember({ operation_id: "op-1", result: null, sent_at: Date.now() });
    const unknown = () => new Response(JSON.stringify({ detail: "Unknown", reason: "find_unknown" }), { status: 404 });
    const answers = [unknown(), unknown(), new Response(JSON.stringify({ running: true }), { status: 200 }), new Response(JSON.stringify({ result: RESULT }), { status: 200 })];
    vi.stubGlobal("fetch", vi.fn(async () => answers.shift()));
    show();
    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByText(/kept running during the reload/)).toBeTruthy();
    await act(async () => { await vi.advanceTimersByTimeAsync(6200); });
    await screen.findByRole("heading", { name: "Winterjacke" });
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("says that a search was cut off once a server should have known it", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    remember({ operation_id: "op-1", result: null, sent_at: Date.now() });
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "Unknown", reason: "find_unknown" }), { status: 404 })));
    show();
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(screen.queryByRole("alert")).toBeNull();
    await act(async () => { await vi.advanceTimersByTimeAsync(20_000); });
    expect((await screen.findByRole("alert")).textContent).toContain("was cut off");
    expect((screen.getByRole("button", { name: "Find it" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("does not wait for a search sent long ago, such as in a tab left open", async () => {
    remember({ operation_id: "op-1", result: null, sent_at: Date.now() - 600_000 });
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "Unknown", reason: "find_unknown" }), { status: 404 })));
    show();
    expect((await screen.findByRole("alert")).textContent).toContain("was cut off");
  });

  it("keeps a search from the moment it is sent, as a server still waking answers only later", async () => {
    const fetchMock = vi.fn(() => new Promise<Response>(() => {}));
    vi.stubGlobal("fetch", fetchMock);
    show();
    fill();
    fireEvent.click(screen.getByRole("button", { name: "Find it" }));
    await screen.findByText(/Working\. This usually takes/);

    const kept = JSON.parse(sessionStorage.getItem(SEARCH_KEY) as string);
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(kept.operation_id).toBe((init.headers as Record<string, string>)["X-Operation-Id"]);
    expect(kept.form.request).toBe("A warm jacket");
    expect(kept.result).toBeNull();
  });

  it("leaves nothing to collect after a refusal, which started no search", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ detail: "Busy", reason: "busy" }), { status: 409 }));
    vi.stubGlobal("fetch", fetchMock);
    show();
    fill();
    fireEvent.click(screen.getByRole("button", { name: "Find it" }));
    await screen.findByRole("alert");
    expect(sessionStorage.getItem(SEARCH_KEY)).toBeNull();
    cleanup();

    fetchMock.mockClear();
    show();
    expect(fetchMock).not.toHaveBeenCalled();
    expect((screen.getByRole("button", { name: "Find it" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("collects a search whose answer was cut off, without a reload or a second request", async () => {
    // A phone can drop the answer while the person is in another app; the server goes on.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const cutOff = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(`${JSON.stringify({ step: "Looking in the shops" })}\n`));
        setTimeout(() => controller.error(new TypeError("network error")), 10);
      },
    });
    const collected = [new Response(JSON.stringify({ running: true }), { status: 200 }), new Response(JSON.stringify({ result: RESULT }), { status: 200 })];
    const fetchMock = vi.fn(async (url: string) => (url === "/api/finds" ? new Response(cutOff, { status: 200 }) : collected.shift()));
    vi.stubGlobal("fetch", fetchMock);
    const onFinished = show();
    fill();
    fireEvent.click(screen.getByRole("button", { name: "Find it" }));
    await act(async () => { await vi.advanceTimersByTimeAsync(100); });

    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByText(/connection dropped/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Looking…" }) as HTMLButtonElement).disabled).toBe(true);
    expect(onFinished).not.toHaveBeenCalled();

    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
    await screen.findByRole("heading", { name: "Winterjacke" });
    expect(screen.queryByRole("alert")).toBeNull();
    expect(fetchMock.mock.calls.filter(([url]) => url === "/api/finds")).toHaveLength(1);
    expect(onFinished).toHaveBeenCalledTimes(1);
    expect((screen.getByRole("button", { name: "Find it" }) as HTMLButtonElement).disabled).toBe(false);
    expect(JSON.parse(sessionStorage.getItem(SEARCH_KEY) as string).operation_id).toBeNull();
  });

  it("asks after a search whose request got no answer at all, as the server may have received it", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fetchMock = vi.fn(async (url: string) => {
      if (url === "/api/finds") throw new TypeError("offline");
      return new Response(JSON.stringify({ result: RESULT }), { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    show();
    fill();
    fireEvent.click(screen.getByRole("button", { name: "Find it" }));
    await act(async () => { await vi.advanceTimersByTimeAsync(100); });
    await screen.findByRole("heading", { name: "Winterjacke" });
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("offers a new search once the server says it never received the lost one", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url === "/api/finds") throw new TypeError("offline");
      return new Response(JSON.stringify({ detail: "Unknown", reason: "find_unknown" }), { status: 404 });
    }));
    show();
    fill();
    fireEvent.click(screen.getByRole("button", { name: "Find it" }));
    await act(async () => { await vi.advanceTimersByTimeAsync(76_000); });
    expect((await screen.findByRole("alert")).textContent).toContain("was cut off");
    expect((screen.getByRole("button", { name: "Find it" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("stops asking when the page goes away, and a reload can still collect the search", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fetchMock = vi.fn(async (url: string) => {
      if (url === "/api/finds") throw new TypeError("offline");
      return new Response(JSON.stringify({ running: true }), { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    show();
    fill();
    fireEvent.click(screen.getByRole("button", { name: "Find it" }));
    await act(async () => { await vi.advanceTimersByTimeAsync(100); });
    cleanup();
    fetchMock.mockClear();
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(fetchMock).not.toHaveBeenCalled();
    expect(JSON.parse(sessionStorage.getItem(SEARCH_KEY) as string).operation_id).toBeTruthy();
  });

  it("keeps the earlier search for a reload when the server refuses a new one", async () => {
    const answers = [
      () => stream({ result: RESULT }),
      () => new Response(JSON.stringify({ detail: "Busy", reason: "busy" }), { status: 409 }),
    ];
    vi.stubGlobal("fetch", async () => (answers.shift() as () => Response)());
    show();
    fill();
    fireEvent.click(screen.getByRole("button", { name: "Find it" }));
    await screen.findByRole("heading", { name: "Winterjacke" });
    fireEvent.change(screen.getByLabelText("What do you need?"), { target: { value: "A hat" } });
    fireEvent.click(screen.getByRole("button", { name: "Find it" }));
    await screen.findByRole("alert");
    cleanup();

    show();
    expect(screen.getByRole("heading", { name: "Winterjacke" })).toBeTruthy();
    expect((screen.getByLabelText("What do you need?") as HTMLTextAreaElement).value).toBe("A warm jacket");
  });

  it("does not show another invite's search, and removes it", () => {
    remember({ invite_scope: "someone-else", operation_id: null, result: RESULT });
    show({ ...SESSION, invite_scope: "mine" });
    expect(screen.queryByRole("heading", { name: "Winterjacke" })).toBeNull();
    expect((screen.getByLabelText("What do you need?") as HTMLTextAreaElement).value).toBe("");
    expect(sessionStorage.getItem(SEARCH_KEY)).toBeNull();
  });

  it.each(["{broken", '{"invite_scope":null,"form":{},"operation_id":null,"result":null}', '{"invite_scope":null,"form":{"request":"x","scope":"everything","line":"men","sizes":"","budget":"1"},"operation_id":null,"result":null}'])(
    "ignores what cannot be read from the tab: %s",
    (stored) => {
      sessionStorage.setItem(SEARCH_KEY, stored);
      vi.stubGlobal("fetch", vi.fn());
      show();
      expect((screen.getByLabelText("What do you need?") as HTMLTextAreaElement).value).toBe("");
      expect((screen.getByRole("button", { name: "Find it" }) as HTMLButtonElement).disabled).toBe(false);
    },
  );

  it("collects a search that was running when the page reloaded, for free", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    remember({ operation_id: "op-1", result: null });
    const answers = [{ running: true }, { result: RESULT }];
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(answers.shift()), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const onFinished = show();

    expect(await screen.findByText(/kept running during the reload/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Looking…" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByLabelText("What do you need?") as HTMLTextAreaElement).value).toBe("A warm jacket");
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });

    await screen.findByRole("heading", { name: "Winterjacke" });
    expect(fetchMock.mock.calls.map((call) => (call as unknown as [string])[0])).toEqual(["/api/finds/op-1", "/api/finds/op-1"]);
    expect(screen.queryByText(/kept running during the reload/)).toBeNull();
    expect(onFinished).toHaveBeenCalled();
    cleanup();

    // Collected once: another reload shows it without asking again.
    fetchMock.mockClear();
    show();
    expect(screen.getByRole("heading", { name: "Winterjacke" })).toBeTruthy();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("says what happened when a running search cannot be collected, and lets the person search again", async () => {
    remember({ operation_id: "op-1", result: null });
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ detail: "Unknown", reason: "find_unknown" }), { status: 404 }));
    vi.stubGlobal("fetch", fetchMock);
    show();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("was cut off");
    expect(alert.textContent).toContain("may still count");
    expect((screen.getByRole("button", { name: "Find it" }) as HTMLButtonElement).disabled).toBe(false);
    expect((screen.getByLabelText("What do you need?") as HTMLTextAreaElement).value).toBe("A warm jacket");
    cleanup();

    fetchMock.mockClear();
    show();
    expect(fetchMock).not.toHaveBeenCalled();
    expect((screen.getByRole("button", { name: "Find it" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("keeps asking through a passing network failure, and stops after five minutes", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    remember({ operation_id: "op-1", result: null });
    const fetchMock = vi.fn(async () => {
      throw new TypeError("offline");
    });
    vi.stubGlobal("fetch", fetchMock);
    show();
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(fetchMock.mock.calls.length).toBeGreaterThan(1);
    expect(screen.queryByRole("alert")).toBeNull();
    await act(async () => { await vi.advanceTimersByTimeAsync(300_000); });
    expect((await screen.findByRole("alert")).textContent).toContain("was cut off");
  });

  it("stops asking when the page goes away", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    remember({ operation_id: "op-1", result: null });
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ running: true }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    show();
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
    cleanup();
    const asked = fetchMock.mock.calls.length;
    await vi.advanceTimersByTimeAsync(10_000);
    expect(fetchMock.mock.calls.length).toBe(asked);
  });

  it("does not start a paid search on its own after a reload", async () => {
    remember({ operation_id: "op-1", result: null });
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ running: true }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    show();
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
    for (const call of fetchMock.mock.calls as unknown as [string, RequestInit | undefined][]) {
      expect(call[1]?.method ?? "GET").toBe("GET");
    }
  });

  it("says in the data notice that a tab keeps its last search", () => {
    render(<PrivacyNotice designOpen={false} findOpen recordsPurgeAt={null} />);
    expect(screen.getByText(/This browser tab also keeps your\s+last search and its results until you close it/)).toBeTruthy();
  });

  it("says when the results were read, which stays true after a reload", async () => {
    remember({ operation_id: null, result: RESULT });
    show();
    expect(screen.getByText(/within the hour before this search/)).toBeTruthy();
    expect(screen.queryByText(/within the last hour/)).toBeNull();
  });

  it("sends the request and shows facts from the shop's page, with what was not checked", async () => {
    const fetchMock = vi.fn(async () => stream({ step: "Looking in the shops for: jacke" }, { result: RESULT }));
    vi.stubGlobal("fetch", fetchMock);
    const onFinished = show();
    fill();
    fireEvent.click(screen.getByRole("radio", { name: "One item" }));
    fireEvent.click(screen.getByRole("radio", { name: "Men's" }));
    fireEvent.change(screen.getByLabelText("Your sizes (optional)"), { target: { value: "L, 32/32," } });
    fireEvent.click(screen.getByRole("button", { name: "Find it" }));

    await screen.findByRole("heading", { name: "Winterjacke" });
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/finds");
    expect(JSON.parse(String(init.body))).toEqual({
      request: "A warm jacket",
      scope: "item",
      line: "men",
      sizes: ["L", "32/32"],
      budget_cents: 15000,
    });
    expect((init.headers as Record<string, string>)["X-Operation-Id"]).toBeTruthy();
    expect(screen.getByRole("link", { name: "View at Shop" }).getAttribute("href")).toBe(FIND.url);
    expect(screen.getByText(/Size not checked for these items/)).toBeTruthy();
    expect(screen.getByText(/Code read every price and stock/)).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Try a look on" })).toBeTruthy();
    expect(screen.getByText(/Clothes you did not ask for stay as in your full-body photo, so wear what you want to see\./)).toBeTruthy();
    expect(onFinished).toHaveBeenCalled();
  });

  it("says nothing about sizes when the page checked them", async () => {
    vi.stubGlobal("fetch", async () => stream({ result: { ...RESULT, finds: [{ ...FIND, size_checked: true }] } }));
    show();
    fill();
    fireEvent.click(screen.getByRole("button", { name: "Find it" }));
    await screen.findByRole("heading", { name: "Winterjacke" });
    expect(screen.queryByText(/Size not checked/)).toBeNull();
  });

  it("shows each step while the agent works, and stops showing them once it is done", async () => {
    let finish: (value: Response) => void = () => {};
    const encoder = new TextEncoder();
    let push: (text: string) => void = () => {};
    let close: () => void = () => {};
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        push = (text) => controller.enqueue(encoder.encode(text));
        close = () => controller.close();
      },
    });
    vi.stubGlobal("fetch", () => new Promise((resolve) => { finish = resolve; }));
    show();
    fill();
    fireEvent.click(screen.getByRole("button", { name: "Find it" }));
    await screen.findByText(/Working\. This usually takes/);
    expect((screen.getByRole("button", { name: "Looking…" }) as HTMLButtonElement).disabled).toBe(true);

    await act(async () => {
      finish(new Response(body, { status: 200 }));
      // A line split across two chunks is still one line.
      push('{"step": "Looking in the ');
      push('shops for: jacke"}\n');
    });
    await screen.findByText("Looking in the shops for: jacke");
    await act(async () => {
      push(`${JSON.stringify({ result: RESULT })}\n`);
      close();
    });
    await screen.findByRole("heading", { name: "Winterjacke" });
    expect(screen.queryByText(/Working\. This usually takes/)).toBeNull();
    expect(screen.queryByText("Looking in the shops for: jacke")).toBeNull();
  });

  it("keeps the results and their try-on when the server refuses a new search", async () => {
    const answers = [
      () => stream({ result: RESULT }),
      () => new Response(JSON.stringify({ detail: "Busy", reason: "busy" }), { status: 409 }),
    ];
    vi.stubGlobal("fetch", async () => (answers.shift() as () => Response)());
    show();
    fill();
    fireEvent.click(screen.getByRole("button", { name: "Find it" }));
    await screen.findByRole("heading", { name: "Winterjacke" });
    fireEvent.click(screen.getByRole("button", { name: "Find it" }));
    expect((await screen.findByRole("alert")).textContent).toContain("still running");
    expect(screen.getByRole("heading", { name: "Winterjacke" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Try a look on" })).toBeTruthy();
  });

  it("says why when nothing suits, and that the request counts", async () => {
    vi.stubGlobal("fetch", async () => stream({ result: { ...RESULT, outcome: "no_match", message: "Nothing warm enough.", find_token: null, finds: [], total_cents: 0 } }));
    show();
    fill();
    fireEvent.click(screen.getByRole("button", { name: "Find it" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Nothing warm enough. This request still counts against your invite.");
    expect(screen.queryByRole("heading", { name: "Try a look on" })).toBeNull();
  });

  it("says that a request did not count when no shop page could be read", async () => {
    vi.stubGlobal("fetch", async () => stream({ result: { ...RESULT, outcome: "shops_unavailable", message: "The shops could not be read right now. Try again later.", find_token: null, finds: [], total_cents: 0, counted: false } }));
    const onFinished = show();
    fill();
    fireEvent.click(screen.getByRole("button", { name: "Find it" }));
    expect((await screen.findByRole("alert")).textContent).toBe("The shops could not be read right now. Try again later. This request did not count against your invite.");
    expect(onFinished).toHaveBeenCalled();
  });

  it("keeps the agent's judgment and the code's checks apart", async () => {
    vi.stubGlobal("fetch", async () => stream({ result: RESULT }));
    show();
    expect(screen.getByText(/Tom Tailor, Tchibo and Giesswein by words in their product addresses/)).toBeTruthy();
    expect(screen.queryByText(/ship to Austria/)).toBeNull();
    fill();
    fireEvent.click(screen.getByRole("button", { name: "Find it" }));
    await screen.findByRole("heading", { name: "Winterjacke" });
    expect(screen.getByText(/It did not check warmth, whether the items go together or your style/)).toBeTruthy();
    expect(screen.getByText("Agent's reason: Warm enough")).toBeTruthy();
    expect(screen.queryByText(/Kind of item not confirmed/)).toBeNull();
    expect(screen.getByText("Outer layer")).toBeTruthy();
  });

  it("says when a shop's title does not confirm the kind of item", async () => {
    vi.stubGlobal("fetch", async () => stream({ result: { ...RESULT, finds: [{ ...FIND, title: "Beach", slot: "footwear", slot_checked: false }] } }));
    show();
    fill();
    fireEvent.click(screen.getByRole("button", { name: "Find it" }));
    await screen.findByRole("heading", { name: "Beach" });
    expect(screen.getByText(/Kind of item not confirmed for these items/)).toBeTruthy();
    expect(screen.getByText("Footwear (not confirmed)")).toBeTruthy();
  });

  it("says what choosing a clothing line leaves out", () => {
    show();
    expect(screen.getByText(/Men's and Women's show only products the shop names that way, or unisex/)).toBeTruthy();
    expect(screen.getByText(/Either also shows products that name no line/)).toBeTruthy();
  });

  it("says what the upper body changes, and what the try-on is made from", async () => {
    vi.stubGlobal("fetch", async () => stream({ result: RESULT }));
    show();
    expect(screen.queryByText(/Upper body limits what the agent chooses/)).toBeNull();
    fireEvent.click(screen.getByRole("radio", { name: "Upper body" }));
    expect(screen.getByText(/A try-on still needs a full-body photo and draws you from head to toe/)).toBeTruthy();
    fill();
    fireEvent.click(screen.getByRole("button", { name: "Find it" }));
    await screen.findByRole("heading", { name: "Winterjacke" });
    expect(screen.getByText(/each product's title and color\. No photo of the product is used, so it may not look like what the shop sells: a pattern or detail that the title does not name can be missing/)).toBeTruthy();
  });

  it("explains a refusal from the server", async () => {
    vi.stubGlobal("fetch", async () => new Response(JSON.stringify({ detail: "Busy", reason: "busy" }), { status: 409 }));
    show();
    fill();
    fireEvent.click(screen.getByRole("button", { name: "Find it" }));
    // Worded so that it stays true after that request ends, as the message stays on screen.
    expect((await screen.findByRole("alert")).textContent).toBe("Another request from this invite was still running. Try again in a minute.");
    expect((screen.getByRole("button", { name: "Find it" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("says so when the answer ends without a result", async () => {
    vi.stubGlobal("fetch", async () => stream({ step: "Looking" }));
    show();
    fill();
    fireEvent.click(screen.getByRole("button", { name: "Find it" }));
    expect((await screen.findByRole("alert")).textContent).toContain("without a result");
  });

  it("refuses a budget of zero and a request of spaces before calling the server", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    show();
    fill("0");
    fireEvent.submit(screen.getByRole("button", { name: "Find it" }).closest("form") as HTMLFormElement);
    expect((await screen.findByRole("alert")).textContent).toContain("greater than zero");
    fill("10");
    fireEvent.change(screen.getByLabelText("What do you need?"), { target: { value: "   " } });
    fireEvent.submit(screen.getByRole("button", { name: "Find it" }).closest("form") as HTMLFormElement);
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("Say what you need"));
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each(["XS,S,M,L,XL,XXL,43", "ABCDEFGHIJKLM"])("refuses the sizes %s before calling the server, and drops none", async (sizes) => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    show();
    fill();
    fireEvent.change(screen.getByLabelText("Your sizes (optional)"), { target: { value: sizes } });
    fireEvent.click(screen.getByRole("button", { name: "Find it" }));
    expect((await screen.findByRole("alert")).textContent).toContain("up to six sizes");
    expect(fetchMock).not.toHaveBeenCalled();
    expect((screen.getByLabelText("Your sizes (optional)") as HTMLInputElement).value).toBe(sizes);
  });

  it("sends six sizes of up to twelve characters in full", async () => {
    const fetchMock = vi.fn(async () => stream({ result: RESULT }));
    vi.stubGlobal("fetch", fetchMock);
    show();
    fill();
    fireEvent.change(screen.getByLabelText("Your sizes (optional)"), { target: { value: "XS,S,M,L,XL,123456789012" } });
    fireEvent.click(screen.getByRole("button", { name: "Find it" }));
    await screen.findByRole("heading", { name: "Winterjacke" });
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(JSON.parse(String(init.body)).sizes).toEqual(["XS", "S", "M", "L", "XL", "123456789012"]);
  });

  it("turns the button off and says why when the mode is closed", () => {
    show({ ...SESSION, find_open: false }, "This invite has used all its requests.");
    expect((screen.getByRole("button", { name: "Find it" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("This invite has used all its requests.")).toBeTruthy();
  });
});
