import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
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
  why: "Warm enough",
};
const RESULT = {
  outcome: "success",
  message: null,
  find_token: "token-1",
  scope: "item",
  budget_cents: 15000,
  total_cents: 9000,
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
    <ShopMode closedText={closedText} onConsent={async () => null} onFinished={onFinished} remaining={8} session={session} visible>
      <p>Feedback here</p>
    </ShopMode>,
  );
  return onFinished;
}

describe("the shopping agent's page", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
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
    expect(screen.getByRole("link", { name: "Shop" }).getAttribute("href")).toBe(FIND.url);
    expect(screen.getByText(/Size not checked for these items/)).toBeTruthy();
    expect(screen.getByText(/Code read every price and stock/)).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Try a look on" })).toBeTruthy();
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

  it("says why when nothing suits, and that the request counts", async () => {
    vi.stubGlobal("fetch", async () => stream({ result: { ...RESULT, outcome: "no_match", message: "Nothing warm enough.", find_token: null, finds: [], total_cents: 0 } }));
    show();
    fill();
    fireEvent.click(screen.getByRole("button", { name: "Find it" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Nothing warm enough. This request still counts against your invite.");
    expect(screen.queryByRole("heading", { name: "Try a look on" })).toBeNull();
  });

  it("explains a refusal from the server", async () => {
    vi.stubGlobal("fetch", async () => new Response(JSON.stringify({ detail: "Busy", reason: "busy" }), { status: 409 }));
    show();
    fill();
    fireEvent.click(screen.getByRole("button", { name: "Find it" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Another request from this invite is still running");
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

  it("turns the button off and says why when the mode is closed", () => {
    show({ ...SESSION, find_open: false }, "This invite has used all its requests.");
    expect((screen.getByRole("button", { name: "Find it" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("This invite has used all its requests.")).toBeTruthy();
  });
});
