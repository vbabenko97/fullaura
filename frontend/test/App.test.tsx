import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "../src/App";
import firstPlanJson from "./fixtures/plan-success-initial.fixture.json?raw";
import replacementPlanJson from "./fixtures/plan-success-replacement.fixture.json?raw";
import reducedBudgetPlanJson from "./fixtures/plan-success-reduced-budget.fixture.json?raw";
import reducedBudgetReplacementPlanJson from "./fixtures/plan-success-reduced-budget-replacement.fixture.json?raw";

/* The catalog fixture is the checked-in non-synthetic catalog. Plan responses below are
 * deterministic UI fixtures, not model output or user recommendations. */
const catalogJson = readFileSync(
  resolve(process.cwd(), "../backend/data/catalog.json"),
  "utf8",
);
const catalogFixture: unknown = JSON.parse(catalogJson);
const FIXTURE_LABEL = "Deterministic API fixture";
const firstPlan: unknown = JSON.parse(firstPlanJson);
const replacementPlan: unknown = JSON.parse(replacementPlanJson);
const reducedBudgetPlan: unknown = JSON.parse(reducedBudgetPlanJson);
const reducedBudgetReplacementPlan: unknown = JSON.parse(reducedBudgetReplacementPlanJson);

function apiResponse(
  payload: unknown,
  status = 200,
  responseHeaders: Record<string, string> = {},
): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers(responseHeaders),
    json: async () => payload,
  } as Response;
}

function successfulResponse(plan: unknown, notices: unknown[] = []): unknown {
  return { outcome: "success", plan, notices, conflicts: [] };
}

function installApiFixtures(
  plans: unknown[],
  previewsEnabled = false,
  previewFetch?: typeof fetch,
  catalog: unknown = catalogFixture,
) {
  if (object(catalog).synthetic !== false) {
    throw new Error("The UI fixture must use the checked-in non-synthetic catalog");
  }
  const requests: unknown[] = [];
  let ageConfirmed = false;
  let planRequestsUsed = 0;
  const fetchFixture = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input);
    if (url === "/api/catalog") return apiResponse(catalog);
    if (url === "/api/session") {
      return apiResponse({
        enabled: true,
        age_confirmed: ageConfirmed,
        plan_requests_used: planRequestsUsed,
        plan_requests_limit: 3,
        plan_requests_remaining: 3 - planRequestsUsed,
      });
    }
    if (url === "/api/session/confirm-age") {
      ageConfirmed = true;
      return apiResponse({
        enabled: true,
        age_confirmed: true,
        plan_requests_used: planRequestsUsed,
        plan_requests_limit: 3,
        plan_requests_remaining: 3 - planRequestsUsed,
      });
    }
    if (url === "/api/previews/status") {
      return apiResponse({
        enabled: previewsEnabled,
        detail: previewsEnabled ? null : "Photo previews are unavailable pending the T-02 feasibility result",
      });
    }
    if (url === "/api/plans") {
      if (!ageConfirmed) return apiResponse({ detail: "Age confirmation required" }, 403);
      const request: unknown = JSON.parse(String(init?.body));
      requests.push(request);
      planRequestsUsed += 1;
      const plan = plans.shift();
      if (plan === undefined) throw new Error(`${FIXTURE_LABEL} has no queued plan response`);
      return apiResponse(plan, 200, {
        "X-Plan-Requests-Remaining": String(3 - planRequestsUsed),
      });
    }
    if (url === "/api/previews" && previewFetch) return previewFetch(input, init);
    throw new Error(`${FIXTURE_LABEL} does not define ${url}`);
  });
  vi.stubGlobal("fetch", fetchFixture);
  return requests;
}

function installObjectUrlMocks() {
  const mocks = {
    createObjectURL: vi.fn(() => "blob:preview"),
    revokeObjectURL: vi.fn(),
  };
  vi.stubGlobal("URL", mocks);
  return mocks;
}

function previewResponse(): Response {
  return {
    ok: true,
    status: 200,
    blob: async () => new Blob(["preview"], { type: "image/png" }),
  } as Response;
}

async function prepareEnabledPreviewFlow(plans: unknown[], previewFetch: typeof fetch) {
  installApiFixtures(plans, true, previewFetch);
  render(<App />);
  await screen.findByText("17 catalog products");
  submitPreferences("Preview source identity", "250");
  fireEvent.click(screen.getByRole("button", { name: "Generate three looks" }));
  await screen.findByText("The three looks passed the catalog and budget checks.");
  fireEvent.change(screen.getByLabelText("Portrait photo"), {
    target: { files: [new File(["portrait"], "portrait.jpg", { type: "image/jpeg" })] },
  });
  fireEvent.change(screen.getByLabelText("Full-body photo"), {
    target: { files: [new File(["full-body"], "full-body.jpg", { type: "image/jpeg" })] },
  });
}

function submitPreferences(styleGoal: string, budget: string) {
  fireEvent.click(screen.getByRole("checkbox", { name: "I confirm that I am 18 or older." }));
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

interface FitFields {
  line?: string;
  garment_type?: string;
  normalized_size?: { system: string; value: string };
}

/* The checked-in catalog with fit fields set per product ID for a test; every other product
 * has no line, garment type or comparable size. */
function catalogWithFit(fields: Record<string, FitFields>): unknown {
  const catalog = JSON.parse(catalogJson) as {
    products: Array<Record<string, unknown> & { id: string; variants: Array<Record<string, unknown>> }>;
  };
  for (const product of catalog.products) {
    const { normalized_size: size = null, ...productFields } = fields[product.id] ?? {};
    Object.assign(product, { line: null, garment_type: null, ...productFields });
    for (const variant of product.variants) variant.normalized_size = size;
  }
  return catalog;
}

function planWithOwnedItems(): unknown {
  const plan = JSON.parse(JSON.stringify(firstPlan)) as {
    looks: Array<{ items: Record<string, unknown> }>;
  };
  plan.looks[0].items.top = { kind: "owned", id: "owned-1" };
  plan.looks[1].items.top = { kind: "owned", id: "owned-2" };
  return plan;
}

function planWithSingleOnePiece(): unknown {
  const plan = JSON.parse(JSON.stringify(firstPlan)) as {
    looks: Array<{ id: string; items: Record<string, { kind: string; id: string }> }>;
    selected_offers: Record<string, string>;
  };
  const look = plan.looks.find((candidate) => candidate.id === "look_3");
  if (!look) throw new Error("Expected the fixture to include look_3");
  const removedBottom = look.items.bottom;
  delete look.items.top;
  delete look.items.bottom;
  look.items.one_piece = {
    kind: "catalog",
    id: "ae-cord-mini-dress-velvet-ember-m",
  };
  plan.selected_offers["ae-cord-mini-dress-velvet-ember-m"] =
    "ae-cord-mini-dress-velvet-ember-m-offer";
  if (
    removedBottom &&
    !plan.looks.some((candidate) =>
      Object.values(candidate.items).some((item) => item.id === removedBottom.id),
    )
  ) {
    delete plan.selected_offers[removedBottom.id];
  }
  return plan;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe(`${FIXTURE_LABEL}: preferences-to-results flow`, () => {
  it("requires age confirmation and disables planning after three session requests", async () => {
    installApiFixtures([
      successfulResponse(firstPlan),
      successfulResponse(reducedBudgetPlan),
      successfulResponse(reducedBudgetReplacementPlan),
    ]);
    render(<App />);
    await screen.findByText("17 catalog products");
    submitPreferences("A limited demo session", "250");

    fireEvent.click(screen.getByRole("button", { name: "Generate three looks" }));
    await screen.findByText("The three looks passed the catalog and budget checks.");
    await screen.findByText("2 of 3 planning requests remain in this session.");
    fireEvent.click(screen.getByRole("button", { name: "Keep JAAMES LANGARMSHIRT in look_2" }));
    fireEvent.change(screen.getByLabelText("Budget in EUR"), { target: { value: "160" } });
    fireEvent.click(screen.getByRole("button", { name: "Update three looks" }));
    await screen.findByText("1 of 3 planning requests remain in this session.");
    fireEvent.click(screen.getByRole("button", { name: "Replace REHMAA LEGGINGS in look_1" }));
    await screen.findByText("The item was replaced. All three looks passed the catalog and budget checks.");

    expect(screen.getByText("0 of 3 planning requests remain in this session.")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Update three looks" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("checkbox", { name: "I confirm that I am 18 or older." }) as HTMLInputElement).disabled).toBe(true);
    expect(
      screen.getByText(/two JPEGs will be sent to Meta after metadata removal/),
    ).toBeTruthy();
  });

  it("generates three looks, sends a changed budget and keeps a lock scoped to one look", async () => {
    const requests = installApiFixtures([
      successfulResponse(firstPlan),
      successfulResponse(reducedBudgetPlan),
    ]);
    render(<App />);
    expect(await screen.findByText("17 catalog products")).toBeTruthy();
    submitPreferences("Relaxed office outfits", "250");
    fireEvent.click(screen.getByRole("button", { name: "Generate three looks" }));

    expect(await screen.findByText("The three looks passed the catalog and budget checks.")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "look 1" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "look 2" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "look 3" })).toBeTruthy();
    expect(screen.getAllByText("MAARKOS HEAVY WEIGHT T-SHIRT")).toHaveLength(2);
    expect(screen.getAllByText("Stock unknown")).not.toHaveLength(0);

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

  it("replaces one item while keeping every other position and the user's locks", async () => {
    const requests = installApiFixtures([
      successfulResponse(firstPlan),
      successfulResponse(replacementPlan),
    ]);
    render(<App />);
    await screen.findByText("17 catalog products");
    submitPreferences("Relaxed office outfits", "250");
    fireEvent.click(screen.getByRole("button", { name: "Generate three looks" }));
    await screen.findByText("The three looks passed the catalog and budget checks.");

    fireEvent.click(
      screen.getByRole("button", { name: "Keep JAAMES LANGARMSHIRT in look_2" }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Replace MAARKOS HEAVY WEIGHT T-SHIRT in look_1" }),
    );

    expect(
      await screen.findByText(
        "The item was replaced. All three looks passed the catalog and budget checks.",
      ),
    ).toBeTruthy();
    expect(screen.getAllByText("JAAMES LANGARMSHIRT")).toHaveLength(2);
    expect(screen.getByText("MAARKOS HEAVY WEIGHT T-SHIRT")).toBeTruthy();
    expect(
      (
        screen.getByRole("button", { name: "Release JAAMES LANGARMSHIRT in look_2" }) as HTMLButtonElement
      ).disabled,
    ).toBe(false);
    expect(
      (
        screen.getByRole("button", { name: "Replace JAAMES LANGARMSHIRT in look_2" }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);

    const request = object(requests[1]);
    expect(request.replacement).toEqual({
      look_id: "look_1",
      slot: "top",
      item: { kind: "catalog", id: "ae-maarkos-moonshadow-m" },
    });
    const state = object(request.state);
    const expectedPlan = { ...object(firstPlan) };
    delete expectedPlan.fixture_label;
    expect(state.plan).toEqual(expectedPlan);
    const keptPositions = (state.locks as Array<Record<string, unknown>>)
      .map((lock) => `${lock.look_id}:${lock.slot}`)
      .sort();
    expect(keptPositions).toEqual([
      "look_1:bottom",
      "look_2:bottom",
      "look_2:top",
      "look_3:bottom",
      "look_3:top",
    ]);
  });

  it("leaves the previous plan visible when replacement fails", async () => {
    installApiFixtures([
      successfulResponse(firstPlan),
      {
        outcome: "generation_failed",
        plan: null,
        conflicts: [],
        violations: [{ rule: "budget", detail: "The proposed look exceeded the budget." }],
        error: null,
      },
    ]);
    render(<App />);
    await screen.findByText("17 catalog products");
    submitPreferences("A replacement failure fixture", "250");
    fireEvent.click(screen.getByRole("button", { name: "Generate three looks" }));
    await screen.findByText("The three looks passed the catalog and budget checks.");

    fireEvent.click(
      screen.getByRole("button", { name: "Replace MAARKOS HEAVY WEIGHT T-SHIRT in look_1" }),
    );
    expect((await screen.findByRole("alert")).textContent).toContain(
      "No replacement passed the catalog checks.",
    );
    expect((await screen.findByRole("alert")).textContent).toContain(
      "The proposed look exceeded the budget.",
    );
    expect((await screen.findByRole("alert")).textContent).toContain(
      "The displayed looks are from the previous successful request.",
    );
    expect(screen.getAllByText("MAARKOS HEAVY WEIGHT T-SHIRT")).toHaveLength(2);
    expect(screen.getByRole("heading", { name: "look 1" })).toBeTruthy();
  });

  it("shows preview controls but keeps them disabled until T-02 passes", async () => {
    installApiFixtures([successfulResponse(firstPlan)]);
    render(<App />);
    await screen.findByText("17 catalog products");
    submitPreferences("Relaxed office outfits", "250");
    fireEvent.click(screen.getByRole("button", { name: "Generate three looks" }));
    await screen.findByText("The three looks passed the catalog and budget checks.");

    expect(screen.getByRole("heading", { name: "Approximate photo preview" })).toBeTruthy();
    expect(screen.getByText("Photo previews are unavailable pending the T-02 feasibility result")).toBeTruthy();
    expect((screen.getByLabelText("Portrait photo") as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByLabelText("Full-body photo") as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Generate approximate preview" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("disables replacement when the catalog has no other item for that position", async () => {
    installApiFixtures([successfulResponse(planWithSingleOnePiece())]);
    render(<App />);
    await screen.findByText("17 catalog products");
    submitPreferences("A look with a one-piece", "250");
    fireEvent.click(screen.getByRole("button", { name: "Generate three looks" }));
    await screen.findByText("The three looks passed the catalog and budget checks.");

    const replaceButton = screen.getByRole("button", {
      name: "Replace CORD-MINI-KLEID MIT RUNDHALS in look_3",
    });
    expect((replaceButton as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("No other catalog or owned item can fill this position.")).toBeTruthy();
  });

  it("clears a generated preview when either source photo changes", async () => {
    const objectUrls = installObjectUrlMocks();
    const previewFetch = vi.fn<typeof fetch>(async () => previewResponse());
    await prepareEnabledPreviewFlow([successfulResponse(firstPlan)], previewFetch);
    fireEvent.click(screen.getByRole("button", { name: "Generate approximate preview" }));
    expect(
      await screen.findByRole("img", { name: "Approximate generated preview for look 1" }),
    ).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Portrait photo"), {
      target: { files: [new File(["new portrait"], "new-portrait.jpg", { type: "image/jpeg" })] },
    });

    expect(screen.queryByRole("img", { name: "Approximate generated preview for look 1" })).toBeNull();
    await waitFor(() => expect(objectUrls.revokeObjectURL).toHaveBeenCalledWith("blob:preview"));
  });

  it("clears a generated preview when a different look is selected", async () => {
    const objectUrls = installObjectUrlMocks();
    const previewFetch = vi.fn<typeof fetch>(async () => previewResponse());
    await prepareEnabledPreviewFlow([successfulResponse(firstPlan)], previewFetch);
    fireEvent.click(screen.getByRole("button", { name: "Generate approximate preview" }));
    expect(
      await screen.findByRole("img", { name: "Approximate generated preview for look 1" }),
    ).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Look to preview"), { target: { value: "look_2" } });

    expect(screen.queryByRole("img", { name: "Approximate generated preview for look 2" })).toBeNull();
    expect((screen.getByLabelText("Look to preview") as HTMLSelectElement).value).toBe("look_2");
    await waitFor(() => expect(objectUrls.revokeObjectURL).toHaveBeenCalledWith("blob:preview"));
  });

  it("clears a preview after replacement changes its source look", async () => {
    const objectUrls = installObjectUrlMocks();
    const previewFetch = vi.fn<typeof fetch>(async () => previewResponse());
    await prepareEnabledPreviewFlow(
      [successfulResponse(firstPlan), successfulResponse(replacementPlan)],
      previewFetch,
    );
    fireEvent.click(screen.getByRole("button", { name: "Generate approximate preview" }));
    expect(
      await screen.findByRole("img", { name: "Approximate generated preview for look 1" }),
    ).toBeTruthy();

    fireEvent.click(
      screen.getByRole("button", { name: "Replace MAARKOS HEAVY WEIGHT T-SHIRT in look_1" }),
    );
    await screen.findByText(
      "The item was replaced. All three looks passed the catalog and budget checks.",
    );

    expect(screen.queryByRole("img")).toBeNull();
    expect((screen.getByLabelText("Portrait photo") as HTMLInputElement).files).toHaveLength(0);
    await waitFor(() => expect(objectUrls.revokeObjectURL).toHaveBeenCalledWith("blob:preview"));
  });

  it("aborts and ignores a preview response that arrives after replacement", async () => {
    installObjectUrlMocks();
    const previewSignal: { current: AbortSignal | null } = { current: null };
    const previewResolver: { current: ((response: Response) => void) | null } = { current: null };
    const previewFetch = vi.fn<typeof fetch>((_input, init) => {
      previewSignal.current = init?.signal ?? null;
      return new Promise<Response>((resolve) => {
        previewResolver.current = resolve;
      });
    });
    await prepareEnabledPreviewFlow(
      [successfulResponse(firstPlan), successfulResponse(replacementPlan)],
      previewFetch,
    );
    fireEvent.click(screen.getByRole("button", { name: "Generate approximate preview" }));
    await waitFor(() => expect(previewFetch).toHaveBeenCalledOnce());

    fireEvent.click(
      screen.getByRole("button", { name: "Replace MAARKOS HEAVY WEIGHT T-SHIRT in look_1" }),
    );
    await screen.findByText(
      "The item was replaced. All three looks passed the catalog and budget checks.",
    );
    expect(previewSignal.current?.aborted).toBe(true);

    const resolvePreview = previewResolver.current;
    if (!resolvePreview) throw new Error("Expected the preview request to remain pending");
    resolvePreview(previewResponse());
    await waitFor(() => expect(screen.queryByRole("img")).toBeNull());
    expect(screen.queryByText("Approximate preview generated. It cannot establish fit or product availability.")).toBeNull();
  });

  it("cancels an in-flight preview request from the interface", async () => {
    const previewSignal: { current: AbortSignal | null } = { current: null };
    const previewFetch = vi.fn<typeof fetch>((_input, init) => {
      previewSignal.current = init?.signal ?? null;
      return new Promise<Response>((_resolve, reject) => {
        previewSignal.current?.addEventListener(
          "abort",
          () => reject(new DOMException("Aborted", "AbortError")),
          { once: true },
        );
      });
    });
    installApiFixtures([successfulResponse(firstPlan)], true, previewFetch);
    render(<App />);
    await screen.findByText("17 catalog products");
    submitPreferences("Relaxed office outfits", "250");
    fireEvent.click(screen.getByRole("button", { name: "Generate three looks" }));
    await screen.findByText("The three looks passed the catalog and budget checks.");
    await waitFor(() => expect((screen.getByLabelText("Portrait photo") as HTMLInputElement).disabled).toBe(false));
    fireEvent.change(screen.getByLabelText("Portrait photo"), {
      target: { files: [new File(["portrait"], "portrait.jpg", { type: "image/jpeg" })] },
    });
    fireEvent.change(screen.getByLabelText("Full-body photo"), {
      target: { files: [new File(["full-body"], "full-body.jpg", { type: "image/jpeg" })] },
    });
    fireEvent.click(screen.getByRole("button", { name: "Generate approximate preview" }));
    await waitFor(() => expect(previewFetch).toHaveBeenCalledOnce());
    fireEvent.click(screen.getByRole("button", { name: "Cancel preview" }));

    const cancellationAlert = await screen.findByRole("alert");
    expect(cancellationAlert.textContent).toBe("Preview generation cancelled.");
    expect(previewSignal.current?.aborted).toBe(true);
  });

  it("rejects a success fixture that changes a position outside the replacement target", async () => {
    const changedPlan = JSON.parse(JSON.stringify(replacementPlan)) as {
      looks: Array<{ items: Record<string, unknown> }>;
    };
    changedPlan.looks[1].items.bottom = {
      kind: "catalog",
      id: "ae-rehmaa-velvet-red-m",
    };
    installApiFixtures([successfulResponse(firstPlan), successfulResponse(changedPlan)]);
    render(<App />);
    await screen.findByText("17 catalog products");
    submitPreferences("A replacement contract fixture", "250");
    fireEvent.click(screen.getByRole("button", { name: "Generate three looks" }));
    await screen.findByText("The three looks passed the catalog and budget checks.");

    fireEvent.click(
      screen.getByRole("button", { name: "Replace MAARKOS HEAVY WEIGHT T-SHIRT in look_1" }),
    );
    expect((await screen.findByRole("alert")).textContent).toContain(
      "The planner did not replace only this item.",
    );
    expect((await screen.findByRole("alert")).textContent).toContain(
      "The displayed looks are from the previous successful request.",
    );
    expect(screen.getAllByText("MAARKOS HEAVY WEIGHT T-SHIRT")).toHaveLength(2);
    expect(screen.getAllByText("JAAMES LANGARMSHIRT")).toHaveLength(1);
  });

  it("does not change locks while an update is in flight", async () => {
    let resolveUpdate: ((response: Response) => void) | undefined;
    let planningCalls = 0;
    let ageConfirmed = false;
    const fetchFixture = vi.fn<typeof fetch>((input) => {
      if (String(input) === "/api/session") {
        return Promise.resolve(apiResponse({
          enabled: true,
          age_confirmed: ageConfirmed,
          plan_requests_used: 0,
          plan_requests_limit: 3,
          plan_requests_remaining: 3,
        }));
      }
      if (String(input) === "/api/session/confirm-age") {
        ageConfirmed = true;
        return Promise.resolve(apiResponse({
          enabled: true,
          age_confirmed: true,
          plan_requests_used: 0,
          plan_requests_limit: 3,
          plan_requests_remaining: 3,
        }));
      }
      if (String(input) === "/api/catalog") return Promise.resolve(apiResponse(catalogFixture));
      if (String(input) === "/api/previews/status") {
        return Promise.resolve(apiResponse({ enabled: false, detail: "Pending T-02" }));
      }
      if (String(input) !== "/api/plans") {
        return Promise.reject(new Error(`Unexpected fixture request: ${String(input)}`));
      }
      planningCalls += 1;
      if (planningCalls === 1) return Promise.resolve(apiResponse(successfulResponse(firstPlan)));
      return new Promise<Response>((resolve) => {
        resolveUpdate = resolve;
      });
    });
    vi.stubGlobal("fetch", fetchFixture);
    render(<App />);
    await screen.findByText("17 catalog products");
    submitPreferences("Lock updates", "250");
    fireEvent.click(screen.getByRole("button", { name: "Generate three looks" }));
    await screen.findByText("The three looks passed the catalog and budget checks.");

    fireEvent.click(screen.getByRole("button", { name: "Update three looks" }));
    const lockButton = screen.getByRole("button", {
      name: "Keep JAAMES LANGARMSHIRT in look_2",
    });
    expect((lockButton as HTMLButtonElement).disabled).toBe(true);
    expect(
      (
        screen.getByRole("button", { name: "Replace JAAMES LANGARMSHIRT in look_2" }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    fireEvent.click(lockButton);
    expect(lockButton.getAttribute("aria-pressed")).toBe("false");

    resolveUpdate?.(apiResponse(successfulResponse(reducedBudgetPlan)));
    await waitFor(() => expect((lockButton as HTMLButtonElement).disabled).toBe(false));
    expect(
      screen.getByRole("button", { name: "Keep JAAMES LANGARMSHIRT in look_2" }).getAttribute("aria-pressed"),
    ).toBe("false");
  });

  it("shows owned-item material notices and explains catalog material checks", async () => {
    installApiFixtures([
      successfulResponse(planWithOwnedItems(), [
        {
          kind: "owned_reported",
          item: { kind: "owned", id: "owned-1" },
          material: "wool",
        },
        {
          kind: "owned_unconfirmed",
          item: { kind: "owned", id: "owned-2" },
          material: "wool",
        },
      ]),
    ]);
    render(<App />);
    await screen.findByText("17 catalog products");
    fireEvent.click(screen.getByRole("button", { name: "Add item" }));
    fireEvent.click(screen.getByRole("button", { name: "Add item" }));
    const colors = screen.getAllByLabelText("Color");
    const descriptions = screen.getAllByLabelText("Description");
    fireEvent.change(colors[0], { target: { value: "navy" } });
    fireEvent.change(descriptions[0], { target: { value: "merino jumper" } });
    fireEvent.change(colors[1], { target: { value: "cream" } });
    fireEvent.change(descriptions[1], { target: { value: "unknown knit" } });
    fireEvent.click(screen.getByText("Avoid materials"));
    fireEvent.click(screen.getByLabelText("wool"));
    submitPreferences("Owned-material notices", "250");
    fireEvent.click(screen.getByRole("button", { name: "Generate three looks" }));

    expect(await screen.findByText("wool reported by you. It is excluded from new purchases.")).toBeTruthy();
    expect(screen.getByText("wool is unconfirmed for this item.")).toBeTruthy();
    expect(
      screen.getByText(/Only materials named in a product's catalog record are checked/),
    ).toBeTruthy();
  });

  it("sends the combined budget mode and shows the unique-item total", async () => {
    const requests = installApiFixtures([successfulResponse(firstPlan)]);
    render(<App />);
    await screen.findByText("17 catalog products");
    submitPreferences("Three work outfits", "450");
    fireEvent.click(screen.getByLabelText("All three looks together"));
    fireEvent.click(screen.getByRole("button", { name: "Generate three looks" }));

    expect(await screen.findByText("The three looks passed the catalog and budget checks.")).toBeTruthy();
    const request = object(requests[0]);
    const state = object(request.state);
    expect(state.budget).toMatchObject({ amount_cents: 45000, mode: "whole_plan" });
    expect(state).not.toHaveProperty("fit");
    expect(screen.getByText(/364,50/)).toBeTruthy();
  });

  it("sends the stated fit in the first request and omits fit fields that are not set", async () => {
    const requests = installApiFixtures([successfulResponse(firstPlan)]);
    render(<App />);
    await screen.findByText("17 catalog products");
    submitPreferences("Looks for a stated wearer", "250");
    fireEvent.click(screen.getByLabelText("Men's"));
    fireEvent.change(screen.getByLabelText("Letter size"), { target: { value: "L" } });
    fireEvent.change(screen.getByLabelText("Waist sizes"), { target: { value: "29, 30,30/34" } });
    fireEvent.change(screen.getByLabelText("EU shoe sizes"), { target: { value: "39, 39.5, 40" } });
    fireEvent.click(screen.getByRole("button", { name: "Generate three looks" }));

    await screen.findByText("The three looks passed the catalog and budget checks.");
    const fit = object(object(object(requests[0]).state).fit);
    expect(fit).toEqual({
      lines: ["men"],
      sizes: [
        { system: "letter", value: "L" },
        { system: "waist_length", value: "29" },
        { system: "waist_length", value: "30" },
        { system: "waist_length", value: "30/34" },
        { system: "eu_shoe", value: "39" },
        { system: "eu_shoe", value: "39.5" },
        { system: "eu_shoe", value: "40" },
      ],
    });
    expect(fit).not.toHaveProperty("garment_types");
    expect(screen.getAllByText(/Size Weite 32 \/ Länge 32/)).not.toHaveLength(0);
  });

  it("carries the stated fit into a replacement request", async () => {
    const requests = installApiFixtures(
      [successfulResponse(firstPlan), successfulResponse(replacementPlan)],
      false,
      undefined,
      catalogWithFit({
        "ae-jaames-black-m-product": { line: "men", normalized_size: { system: "letter", value: "M" } },
      }),
    );
    render(<App />);
    await screen.findByText("17 catalog products");
    submitPreferences("Fit through a replacement", "250");
    fireEvent.click(screen.getByLabelText("Men's"));
    fireEvent.click(screen.getByLabelText("Trousers"));
    fireEvent.click(screen.getByLabelText("Jeans"));
    fireEvent.change(screen.getByLabelText("Letter size"), { target: { value: "M" } });
    fireEvent.change(screen.getByLabelText("EU shoe sizes"), { target: { value: "39.5, 40" } });
    fireEvent.click(screen.getByRole("button", { name: "Generate three looks" }));
    await screen.findByText("The three looks passed the catalog and budget checks.");

    fireEvent.click(
      screen.getByRole("button", { name: "Replace MAARKOS HEAVY WEIGHT T-SHIRT in look_1" }),
    );
    await screen.findByText(
      "The item was replaced. All three looks passed the catalog and budget checks.",
    );

    const expectedFit = {
      lines: ["men"],
      garment_types: ["trousers", "jeans"],
      sizes: [
        { system: "letter", value: "M" },
        { system: "eu_shoe", value: "39.5" },
        { system: "eu_shoe", value: "40" },
      ],
    };
    expect(object(object(requests[0]).state).fit).toEqual(expectedFit);
    const replacementRequest = object(requests[1]);
    expect(replacementRequest.replacement).toMatchObject({ look_id: "look_1", slot: "top" });
    expect(object(replacementRequest.state).fit).toEqual(expectedFit);
  });

  it.each([
    ["EU shoe sizes", "42.7, 43", "42.7 is not an EU shoe size."],
    ["Waist sizes", "33/3", "33/3 is not a waist size."],
    [
      "EU shoe sizes",
      "36, 36.5, 37, 37.5, 38, 38.5, 39, 39.5, 40, 40.5, 41, 41.5, 42",
      "Enter no more than 12 sizes in total; you entered 13.",
    ],
  ])("shows an error and sends no request for %s %s", async (field, value, expectedMessage) => {
    const requests = installApiFixtures([successfulResponse(firstPlan)]);
    render(<App />);
    await screen.findByText("17 catalog products");
    submitPreferences("An invalid size", "250");
    fireEvent.change(screen.getByLabelText(field), { target: { value } });
    fireEvent.click(screen.getByRole("button", { name: "Generate three looks" }));

    expect((await screen.findByRole("alert")).textContent).toContain(expectedMessage);
    expect(requests).toHaveLength(0);
    expect(screen.queryByRole("heading", { name: "look 1" })).toBeNull();
  });

  it("disables replacement when no catalog variant fits the stated fit for the position", async () => {
    installApiFixtures(
      [successfulResponse(firstPlan)],
      false,
      undefined,
      catalogWithFit({
        "ae-jaames-black-m-product": { line: "men", normalized_size: { system: "letter", value: "S" } },
        "ae-loukaa-dark-earth-m-product": { line: "unisex", normalized_size: { system: "letter", value: "M" } },
        "ae-wool-cardigan-claystone-m-product": { normalized_size: { system: "letter", value: "S" } },
        "ae-kaavo-lolite-32-32-product": {
          line: "women",
          garment_type: "jeans",
          normalized_size: { system: "waist_length", value: "32/32" },
        },
        "ae-lenovaa-dark-steel-m-product": {
          line: "women",
          garment_type: "skirt",
          normalized_size: { system: "letter", value: "S" },
        },
      }),
    );
    render(<App />);
    await screen.findByText("17 catalog products");
    submitPreferences("Replacement under a stated fit", "250");
    fireEvent.click(screen.getByLabelText("Women's"));
    fireEvent.click(screen.getByLabelText("Skirts"));
    fireEvent.change(screen.getByLabelText("Letter size"), { target: { value: "S" } });
    fireEvent.click(screen.getByRole("button", { name: "Generate three looks" }));
    await screen.findByText("The three looks passed the catalog and budget checks.");

    // Tops: one is men's, the unisex one is size M, and one has no line.
    const topReplace = screen.getByRole("button", {
      name: "Replace MAARKOS HEAVY WEIGHT T-SHIRT in look_1",
    }) as HTMLButtonElement;
    expect(topReplace.disabled).toBe(true);
    expect(screen.getAllByText("No other catalog or owned item can fill this position.")).not.toHaveLength(0);
    // Bottoms: the women's jeans fail the garment type, and the women's skirt in S fits.
    const bottomReplace = screen.getByRole("button", {
      name: "Replace AASTER CHINOHOSE in look_1",
    }) as HTMLButtonElement;
    expect(bottomReplace.disabled).toBe(false);
  });

  it.each([
    ["fit", "Kept items conflict with these constraints: ae-maarkos-moonshadow-m is from the men line"],
    ["look_composition", "No plan can meet these constraints: No catalog variant that passes the checks"],
  ])("names kept items in an infeasible update only when a %s conflict involves them", async (rule, prefix) => {
    const detail =
      rule === "fit"
        ? "ae-maarkos-moonshadow-m is from the men line, not women"
        : "No catalog variant that passes the checks, and no owned item, completes a top and bottom or a one_piece";
    installApiFixtures([
      successfulResponse(firstPlan),
      { outcome: "infeasible", plan: null, conflicts: [{ rule, detail }] },
    ]);
    render(<App />);
    await screen.findByText("17 catalog products");
    submitPreferences("Relaxed office outfits", "250");
    fireEvent.click(screen.getByRole("button", { name: "Generate three looks" }));
    await screen.findByText("The three looks passed the catalog and budget checks.");

    fireEvent.click(screen.getByRole("button", { name: "Keep MAARKOS HEAVY WEIGHT T-SHIRT in look_1" }));
    fireEvent.click(screen.getByRole("button", { name: "Update three looks" }));

    expect((await screen.findByRole("alert")).textContent).toContain(prefix);
  });

  it.each([
    [
      "infeasible",
      {
        outcome: "infeasible",
        plan: null,
        conflicts: [
          {
            rule: "look_composition",
            detail: "No catalog variant that passes the checks, and no owned item, completes a top and bottom or a one_piece",
          },
        ],
      },
      /^No plan can meet these constraints: No catalog variant that passes the checks/,
    ],
    ["generation_failed", { outcome: "generation_failed", plan: null, conflicts: [] }, /No plan passed the catalog checks/],
    ["provider_timeout", { outcome: "provider_timeout", plan: null, conflicts: [] }, /planning request timed out/i],
    ["provider_error", { outcome: "provider_error", plan: null, conflicts: [] }, /planning provider could not complete/i],
  ])("renders the %s planning failure fixture", async (_name, response, expectedMessage) => {
    installApiFixtures([response]);
    render(<App />);
    await screen.findByText("17 catalog products");
    submitPreferences("A checked failure state", "250");
    fireEvent.click(screen.getByRole("button", { name: "Generate three looks" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(expectedMessage);
    expect(screen.queryByRole("heading", { name: "look 1" })).toBeNull();
  });

  it("labels an unconfigured development model after the API fixture returns 503", async () => {
    const fetchFixture = vi.fn<typeof fetch>(async (input) => {
      if (String(input) === "/api/session") {
        return apiResponse({
          enabled: true,
          age_confirmed: false,
          plan_requests_used: 0,
          plan_requests_limit: 3,
          plan_requests_remaining: 3,
        });
      }
      if (String(input) === "/api/session/confirm-age") {
        return apiResponse({
          enabled: true,
          age_confirmed: true,
          plan_requests_used: 0,
          plan_requests_limit: 3,
          plan_requests_remaining: 3,
        });
      }
      if (String(input) === "/api/catalog") return apiResponse(catalogFixture);
      if (String(input) === "/api/previews/status") {
        return apiResponse({ enabled: false, detail: "Pending T-02" });
      }
      return apiResponse({ detail: "The planning model is not configured" }, 503);
    });
    vi.stubGlobal("fetch", fetchFixture);
    render(<App />);
    await screen.findByText("17 catalog products");
    submitPreferences("A configured model is required", "250");
    fireEvent.click(screen.getByRole("button", { name: "Generate three looks" }));
    expect((await screen.findByRole("alert")).textContent).toContain(
      "The planning model is not configured",
    );
  });

  it("shows the network failure state when the planning request rejects", async () => {
    const fetchFixture = vi.fn<typeof fetch>(async (input) => {
      if (String(input) === "/api/session") {
        return apiResponse({
          enabled: true,
          age_confirmed: false,
          plan_requests_used: 0,
          plan_requests_limit: 3,
          plan_requests_remaining: 3,
        });
      }
      if (String(input) === "/api/session/confirm-age") {
        return apiResponse({
          enabled: true,
          age_confirmed: true,
          plan_requests_used: 0,
          plan_requests_limit: 3,
          plan_requests_remaining: 3,
        });
      }
      if (String(input) === "/api/catalog") return apiResponse(catalogFixture);
      if (String(input) === "/api/previews/status") {
        return apiResponse({ enabled: false, detail: "Pending T-02" });
      }
      if (String(input) === "/api/plans") {
        throw new TypeError("fixture network unavailable");
      }
      throw new TypeError("fixture network unavailable");
    });
    vi.stubGlobal("fetch", fetchFixture);
    render(<App />);
    await screen.findByText("17 catalog products");
    submitPreferences("A network failure fixture", "250");
    fireEvent.click(screen.getByRole("button", { name: "Generate three looks" }));
    expect((await screen.findByRole("alert")).textContent).toContain(
      "The request could not be completed. Check the backend and try again.",
    );
  });
});
