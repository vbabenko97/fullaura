import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "../src/App";
import firstPlanJson from "./fixtures/plan-success-initial.fixture.json?raw";
import reducedBudgetPlanJson from "./fixtures/plan-success-reduced-budget.fixture.json?raw";

/* The catalog fixture is the checked-in non-synthetic catalog. Plan responses below are
 * deterministic UI fixtures, not model output or user recommendations. */
const catalogJson = readFileSync(
  resolve(process.cwd(), "../backend/data/catalog.json"),
  "utf8",
);
const catalogFixture: unknown = JSON.parse(catalogJson);
const FIXTURE_LABEL = "Deterministic API fixture";
const firstPlan: unknown = JSON.parse(firstPlanJson);
const reducedBudgetPlan: unknown = JSON.parse(reducedBudgetPlanJson);

function apiResponse(payload: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => payload,
  } as Response;
}

function successfulResponse(plan: unknown): unknown {
  return { outcome: "success", plan, conflicts: [] };
}

function installApiFixtures(plans: unknown[]) {
  if (object(catalogFixture).synthetic !== false) {
    throw new Error("The UI fixture must use the checked-in non-synthetic catalog");
  }
  const requests: unknown[] = [];
  const fetchFixture = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input);
    if (url === "/api/catalog") return apiResponse(catalogFixture);
    if (url === "/api/plans") {
      const request: unknown = JSON.parse(String(init?.body));
      requests.push(request);
      const plan = plans.shift();
      if (plan === undefined) throw new Error(`${FIXTURE_LABEL} has no queued plan response`);
      return apiResponse(plan);
    }
    throw new Error(`${FIXTURE_LABEL} does not define ${url}`);
  });
  vi.stubGlobal("fetch", fetchFixture);
  return requests;
}

function submitPreferences(styleGoal: string, budget: string) {
  fireEvent.change(screen.getByLabelText("What should the looks work for?"), {
    target: { value: styleGoal },
  });
  fireEvent.change(screen.getByLabelText("Budget in EUR"), { target: { value: budget } });
}

function object(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error("Expected fixture request object");
  }
  return value as Record<string, unknown>;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe(`${FIXTURE_LABEL}: preferences-to-results flow`, () => {
  it("generates three looks, sends a changed budget and keeps a lock scoped to one look", async () => {
    const requests = installApiFixtures([
      successfulResponse(firstPlan),
      successfulResponse(reducedBudgetPlan),
    ]);
    render(<App />);
    expect(await screen.findByText("15 catalog products")).toBeTruthy();
    submitPreferences("Relaxed office outfits", "250");
    fireEvent.click(screen.getByRole("button", { name: "Generate three looks" }));

    expect(await screen.findByText("The three looks passed the catalog and budget checks.")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "look 1" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "look 2" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "look 3" })).toBeTruthy();
    expect(screen.getAllByText("MAARKOS HEAVY WEIGHT T-SHIRT")).toHaveLength(2);

    fireEvent.click(
      screen.getByRole("button", { name: "Keep MAARKOS HEAVY WEIGHT T-SHIRT in look_1" }),
    );
    fireEvent.change(screen.getByLabelText("Budget in EUR"), { target: { value: "160" } });
    fireEvent.click(screen.getByRole("button", { name: "Update three looks" }));

    expect(await screen.findAllByText("REHMAA LEGGINGS")).toHaveLength(2);
    await waitFor(() => expect(requests).toHaveLength(2));
    const firstRequest = object(requests[0]);
    const firstState = object(firstRequest.state);
    const firstBudget = object(firstState.budget);
    expect(firstBudget).toMatchObject({ amount_cents: 25000, mode: "per_look" });

    const secondRequest = object(requests[1]);
    const secondState = object(secondRequest.state);
    const secondBudget = object(secondState.budget);
    expect(secondBudget).toMatchObject({ amount_cents: 16000, mode: "per_look" });
    expect(secondState.locks).toEqual([
      {
        look_id: "look_1",
        slot: "top",
        item: { kind: "catalog", id: "ae-maarkos-moonshadow-m" },
      },
    ]);
    expect(
      screen
        .getByRole("button", { name: "Release MAARKOS HEAVY WEIGHT T-SHIRT in look_1" })
        .getAttribute("aria-pressed"),
    ).toBe("true");
    expect(screen.getByText(/234,40/)).toBeTruthy();
  });

  it("sends the combined budget mode and shows the unique-item total", async () => {
    const requests = installApiFixtures([successfulResponse(firstPlan)]);
    render(<App />);
    await screen.findByText("15 catalog products");
    submitPreferences("Three work outfits", "450");
    fireEvent.click(screen.getByLabelText("All three looks together"));
    fireEvent.click(screen.getByRole("button", { name: "Generate three looks" }));

    expect(await screen.findByText("The three looks passed the catalog and budget checks.")).toBeTruthy();
    const request = object(requests[0]);
    const state = object(request.state);
    expect(state.budget).toMatchObject({ amount_cents: 45000, mode: "whole_plan" });
    expect(screen.getByText(/364,50/)).toBeTruthy();
  });

  it.each([
    ["infeasible", { outcome: "infeasible", plan: null, conflicts: [{ detail: "Kept items exceed this budget." }] }, /Kept items conflict/],
    ["generation_failed", { outcome: "generation_failed", plan: null, conflicts: [] }, /No plan passed the catalog checks/],
    ["provider_timeout", { outcome: "provider_timeout", plan: null, conflicts: [] }, /planning request timed out/i],
    ["provider_error", { outcome: "provider_error", plan: null, conflicts: [] }, /planning provider could not complete/i],
  ])("renders the %s planning failure fixture", async (_name, response, expectedMessage) => {
    installApiFixtures([response]);
    render(<App />);
    await screen.findByText("15 catalog products");
    submitPreferences("A checked failure state", "250");
    fireEvent.click(screen.getByRole("button", { name: "Generate three looks" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(expectedMessage);
    expect(screen.queryByRole("heading", { name: "look 1" })).toBeNull();
  });

  it("labels an unconfigured development model after the API fixture returns 503", async () => {
    const fetchFixture = vi.fn<typeof fetch>(async (input) => {
      if (String(input) === "/api/catalog") return apiResponse(catalogFixture);
      return apiResponse({ detail: "BASETEN_API_KEY is not set" }, 503);
    });
    vi.stubGlobal("fetch", fetchFixture);
    render(<App />);
    await screen.findByText("15 catalog products");
    submitPreferences("A configured model is required", "250");
    fireEvent.click(screen.getByRole("button", { name: "Generate three looks" }));
    expect((await screen.findByRole("alert")).textContent).toContain(
      "The planning model is not configured in this development environment.",
    );
  });

  it("shows the network failure state when the planning request rejects", async () => {
    const fetchFixture = vi.fn<typeof fetch>(async (input) => {
      if (String(input) === "/api/catalog") return apiResponse(catalogFixture);
      throw new TypeError("fixture network unavailable");
    });
    vi.stubGlobal("fetch", fetchFixture);
    render(<App />);
    await screen.findByText("15 catalog products");
    submitPreferences("A network failure fixture", "250");
    fireEvent.click(screen.getByRole("button", { name: "Generate three looks" }));
    expect((await screen.findByRole("alert")).textContent).toContain(
      "The request could not be completed. Check the backend and try again.",
    );
  });
});
