import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../src/App";
import { normalizePhoto } from "../src/photos";
import firstPlanJson from "./fixtures/plan-success-initial.fixture.json?raw";
import replacementPlanJson from "./fixtures/plan-success-replacement.fixture.json?raw";
import reducedBudgetPlanJson from "./fixtures/plan-success-reduced-budget.fixture.json?raw";

vi.mock("../src/photos", async (importOriginal) => {
  const original = await importOriginal<typeof import("../src/photos")>();
  return {
    ...original,
    normalizePhoto: vi.fn(async (file: File) => {
      if (!original.ACCEPTED_PHOTO_TYPES.includes(file.type)) {
        throw new original.PhotoError(original.UNSUPPORTED_PHOTO);
      }
      return new Blob(["redrawn jpeg"], { type: "image/jpeg" });
    }),
  };
});

/* The catalog is the checked-in non-synthetic catalog. Plan responses are deterministic UI
 * fixtures, not model output or recommendations. */
const catalog: unknown = JSON.parse(readFileSync(resolve(process.cwd(), "../backend/data/catalog.json"), "utf8"));
const FIXTURE_LABEL = "Deterministic API fixture";
const firstPlan: unknown = JSON.parse(firstPlanJson);
const replacementPlan: unknown = JSON.parse(replacementPlanJson);
const reducedBudgetPlan: unknown = JSON.parse(reducedBudgetPlanJson);

type Body = Record<string, unknown>;

function json(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), { status, headers: { "Content-Type": "application/json" } });
}

function object(value: unknown): Body {
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new Error("Expected an object");
  return value as Body;
}

interface Server {
  plans: unknown[];
  requests: {
    url: string;
    body: Body | FormData | null;
    headers: Record<string, string>;
    signal: AbortSignal | null;
  }[];
  session: Body;
  token: number;
  expireToken: () => void;
  previewOutcome: "image" | "budget" | "provider" | "timeout";
  restoreFails: boolean;
  /** A restore never reaches the server, as when the connection drops. */
  restoreOffline: boolean;
  /** When set, a restore answers only once this settles, as on a slow connection. */
  restoreGate: Promise<void> | null;
  /** When set, a plan request answers only once this settles, as a slow model would. */
  planGate: Promise<void> | null;
  catalogFailures: number;
  consentFails: boolean;
  /** When set, a try-on answers only once this settles, as a slow provider would. */
  previewGate: Promise<void> | null;
  /** When set, a try-on reaches the token check only once this settles, as on a slow connection. */
  previewEntryGate: Promise<void> | null;
  previewsAnswered: number;
  concepts: unknown[];
  /** The token of the concept the server holds now; a try-on with another one is refused. */
  conceptToken: string | null;
}

/** A promise the test settles by hand, for responses that arrive late. */
function deferred<T = void>() {
  let resolve: (value: T) => void = () => undefined;
  const promise = new Promise<T>((settle) => {
    resolve = settle;
  });
  return { promise, resolve };
}

function installServer(
  options: {
    invited?: boolean;
    aged?: boolean;
    plans?: unknown[];
    accounting?: boolean;
    catalog?: unknown;
    design?: boolean;
    concepts?: unknown[];
  } = {},
): Server {
  const server: Server = {
    plans: options.plans ?? [],
    concepts: options.concepts ?? [],
    conceptToken: null,
    requests: [],
    token: 0,
    session: {
      beta: true,
      invited: options.invited ?? true,
      accounting: options.accounting ?? true,
      // Like the server: while the store cannot be read, it cannot say the age was confirmed.
      age_confirmed: (options.accounting ?? true) && (options.aged ?? true),
      photo_consent: false,
      plan_requests_used: 0,
      plan_requests_limit: 8,
      preview_requests_used: 0,
      preview_requests_limit: 12,
      planning_open: options.accounting ?? true,
      previews_open: true,
      pilot_ends_at: null,
      pilot_ended: false,
      records_purge_at: "2026-11-09T10:00:00+01:00",
      feedback_ids: [],
      has_plan: false,
      design_open: options.design ?? false,
    },
    expireToken: () => {
      server.token += 100;
    },
    previewOutcome: "image",
    restoreFails: false,
    restoreOffline: false,
    restoreGate: null,
    planGate: null,
    catalogFailures: 0,
    consentFails: false,
    previewGate: null,
    previewEntryGate: null,
    previewsAnswered: 0,
  };
  const currentToken = () => `token-${server.token}`;
  const success = (plan: unknown) => ({
    outcome: "success",
    plan,
    plan_token: currentToken(),
    catalog_id: object(catalog).id,
    notices: [],
    conflicts: [],
    violations: [],
    error: null,
  });
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input);
    const headers = Object.fromEntries(new Headers(init?.headers).entries());
    const raw = init?.body;
    const body = raw instanceof FormData ? raw : typeof raw === "string" ? object(JSON.parse(raw)) : null;
    server.requests.push({ url, body, headers, signal: init?.signal ?? null });
    const session = server.session;
    if (url === "/api/session") return json(session);
    if (url === "/api/session/invite") {
      if (String(object(body).code).length > 64) return json({ detail: "not valid", reason: "invalid" }, 422);
      if (object(body).code !== "good-invite") return json({ detail: "invalid", reason: "invite_invalid" }, 403);
      session.invited = true;
      return json(session);
    }
    if (url === "/api/session/confirm-age") {
      session.age_confirmed = true;
      return json(session);
    }
    if (url === "/api/session/photo-consent") {
      if (server.consentFails) return json({ detail: "store down", reason: "accounting" }, 503);
      session.photo_consent = object(body).consent;
      return json(session);
    }
    if (url === "/api/catalog") {
      if (server.catalogFailures > 0) {
        server.catalogFailures -= 1;
        return json({ detail: "Service Unavailable", reason: null }, 503);
      }
      return json(options.catalog ?? catalog);
    }
    if (url === "/api/plans") {
      const request = object(body);
      if (!headers["x-operation-id"]) return json({ detail: "no id", reason: "operation_id" }, 400);
      if (request.plan_token !== null && request.plan_token !== currentToken()) {
        return json({ detail: "expired", reason: "plan_expired" }, 409);
      }
      if (!session.planning_open) return json({ detail: "paused", reason: "accounting_unavailable" }, 503);
      if (server.planGate) await server.planGate;
      session.plan_requests_used = Number(session.plan_requests_used) + 1;
      const next = server.plans.shift();
      if (next === undefined) throw new Error(`${FIXTURE_LABEL} has no queued plan`);
      if (object(next).outcome) return json(next);
      server.token += 1;
      return json(success(next));
    }
    if (url === "/api/plans/restore") {
      if (server.restoreOffline) throw new TypeError("Failed to fetch");
      if (server.restoreGate) await server.restoreGate;
      // Like the server, refuse any field beyond the saved brief, state and catalog ID.
      if (Object.keys(object(body)).sort().join() !== "brief,catalog_id,state") {
        return json({ detail: "extra fields", reason: "invalid" }, 422);
      }
      const state = object(object(body).state);
      const looks = object(state.plan).looks as Body[];
      // Like constraints.py, a lock whose item is not at its position in the plan is a violation.
      const brokenLocks = (state.locks as Body[]).filter((lock) => {
        const look = looks.find((candidate) => candidate.id === lock.look_id);
        const placed = look ? (object(look.items)[String(lock.slot)] as Body | undefined) : undefined;
        return placed?.kind !== object(lock.item).kind || placed?.id !== object(lock.item).id;
      });
      const violations = [
        ...(server.restoreFails ? [{ rule: "budget", detail: "over budget" }] : []),
        ...brokenLocks.map((lock) => ({
          rule: "lock",
          detail: `${String(object(lock.item).id)} is locked in ${String(lock.look_id)} as ${String(lock.slot)}`,
        })),
      ];
      if (violations.length) {
        return json({
          outcome: "restore_failed",
          plan: null,
          violations,
          conflicts: [],
          notices: [],
          error: null,
          catalog_id: object(catalog).id,
          catalog_changed: true,
        });
      }
      server.token += 1;
      return json({ ...success(state.plan), catalog_changed: false });
    }
    if (url === "/api/concepts") {
      const request = object(body);
      if (!headers["x-operation-id"]) return json({ detail: "no id", reason: "operation_id" }, 400);
      // Like the server, refuse any field beyond the brief and the four checked state fields.
      const stateFields = Object.keys(object(request.state));
      if (
        Object.keys(request).sort().join() !== "brief,state" ||
        stateFields.some((field) => !["budget", "material_exclusions", "footwear", "fit"].includes(field))
      ) {
        return json({ detail: "extra fields", reason: "invalid" }, 422);
      }
      session.plan_requests_used = Number(session.plan_requests_used) + 1;
      const next = server.concepts.shift();
      if (next === undefined) throw new Error(`${FIXTURE_LABEL} has no queued concept`);
      if (object(next).outcome !== "success") return json(next);
      server.conceptToken = `concept-token-${server.requests.filter((entry) => entry.url === url).length}`;
      return json({ ...object(next), concept_token: server.conceptToken });
    }
    if (url === "/api/previews") {
      if (server.previewEntryGate) await server.previewEntryGate;
      const conceptToken = headers["x-concept-token"];
      if (conceptToken !== undefined && conceptToken !== server.conceptToken) {
        return json({ detail: "replaced", reason: "concept_expired" }, 409);
      }
      if (conceptToken === undefined && headers["x-plan-token"] !== currentToken()) {
        return json({ detail: "expired", reason: "plan_expired" }, 409);
      }
      if (server.previewOutcome === "budget") return json({ detail: "budget", reason: "budget" }, 503);
      if (server.previewOutcome === "timeout") {
        session.preview_requests_used = Number(session.preview_requests_used) + 1;
        return json({ detail: "The try-on took too long", reason: "timeout" }, 504);
      }
      if (server.previewOutcome === "provider") {
        session.preview_requests_used = Number(session.preview_requests_used) + 1;
        return json({ detail: "The image provider could not complete this try-on", reason: "provider" }, 502);
      }
      if (server.previewGate) await server.previewGate;
      server.previewsAnswered += 1;
      session.preview_requests_used = Number(session.preview_requests_used) + 1;
      return new Response(new TextEncoder().encode("preview"), { status: 200, headers: { "Content-Type": "image/png" } });
    }
    if (url === "/api/feedback") {
      session.feedback_ids = ["0f".repeat(16)];
      return json({ id: "0f".repeat(16) }, 201);
    }
    if (url.startsWith("/api/feedback/")) {
      session.feedback_ids = [];
      return new Response(null, { status: 204 });
    }
    throw new Error(`${FIXTURE_LABEL} does not define ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return server;
}

function planRequests(server: Server): Body[] {
  return server.requests.filter((entry) => entry.url === "/api/plans").map((entry) => object(entry.body));
}

async function generate(styleGoal = "Relaxed office outfits", budget = "250") {
  await screen.findByText(/products\. Prices checked/);
  fireEvent.change(screen.getByLabelText("What should the looks work for?"), { target: { value: styleGoal } });
  fireEvent.change(screen.getByLabelText("Budget in EUR"), { target: { value: budget } });
  fireEvent.click(screen.getByRole("button", { name: "Generate three looks" }));
  await screen.findByText(/The three looks passed the catalog checks/);
}

const CURRENT_PLAN_KEY = "fullaura.current-plan.v1";

/** The working plan this tab kept before a reload, as the page stores it; returns the stored text. */
function storeWorkingPlan(plan: unknown, locks: Body[] = []): string {
  const text = JSON.stringify({
    id: "token-0-working",
    saved_at: "2026-10-30T12:00:00.000Z",
    name: "Relaxed office outfits",
    brief: { style_goal: "Relaxed office outfits", preferred_colors: [], occasion: null, season: null },
    state: {
      budget: { amount_cents: 25000, mode: "per_look" },
      material_exclusions: [],
      footwear: "closed",
      owned_items: [],
      locks,
      plan,
    },
    catalog_id: object(catalog).id,
    shopping_list: [],
  });
  sessionStorage.setItem(CURRENT_PLAN_KEY, text);
  return text;
}

beforeEach(() => {
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:tryon"), revokeObjectURL: vi.fn() }));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  localStorage.clear();
  sessionStorage.clear();
  window.location.hash = "";
});

describe(`${FIXTURE_LABEL}: access`, () => {
  it("asks for an invite, then the age confirmation, before planning", async () => {
    installServer({ invited: false, aged: false });
    render(<App />);
    fireEvent.change(await screen.findByLabelText("Invite code"), { target: { value: "wrong-invite" } });
    fireEvent.click(screen.getByRole("button", { name: "Open invite" }));
    expect(await screen.findByText(/This invite code is not valid/)).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Invite code"), { target: { value: "good-invite" } });
    fireEvent.click(screen.getByRole("button", { name: "Open invite" }));
    const confirm = await screen.findByRole("button", { name: "Continue" });
    expect((confirm as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("checkbox", { name: "I confirm that I am 18 or older." }));
    fireEvent.click(confirm);
    expect(await screen.findByRole("heading", { name: "Your preferences" })).toBeTruthy();
    expect(screen.queryByText(/This invite code is not valid/)).toBeNull();
  });

  it("opens the invite in the link fragment and removes it from the address", async () => {
    window.location.hash = "#invite=good-invite";
    const server = installServer({ invited: false });
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Your preferences" })).toBeTruthy();
    expect(object(server.requests[0].body)).toEqual({ code: "good-invite" });
    expect(window.location.hash).toBe("");
  });

  it("opens an invite link pasted into a tab that is already open", async () => {
    const server = installServer({ invited: false });
    render(<App />);
    await screen.findByLabelText("Invite code");
    window.location.hash = "#invite=good-invite";
    expect(await screen.findByRole("heading", { name: "Your preferences" })).toBeTruthy();
    expect(server.requests.filter((entry) => entry.url === "/api/session/invite")).toHaveLength(1);
    expect(window.location.hash).toBe("");
  });

  it("shows a refused invite link pasted over an open workspace, and keeps the workspace", async () => {
    installServer();
    render(<App />);
    await screen.findByRole("heading", { name: "Your preferences" });
    window.location.hash = "#invite=wrong-invite";
    expect(await screen.findByText(/This invite code is not valid/)).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Your preferences" })).toBeTruthy();
  });

  it("calls a damaged invite link not valid instead of stopping on the loading line", async () => {
    window.location.hash = "#invite=%E0%A4%A";
    installServer({ invited: false });
    render(<App />);
    expect(await screen.findByText(/This invite code is not valid/)).toBeTruthy();
  });

  it("explains a request the server could not read", async () => {
    installServer({ invited: false });
    render(<App />);
    fireEvent.change(await screen.findByLabelText("Invite code"), { target: { value: "x".repeat(65) } });
    fireEvent.click(screen.getByRole("button", { name: "Open invite" }));
    expect(await screen.findByText(/Check what you entered/)).toBeTruthy();
    expect(screen.queryByText(/status 422/)).toBeNull();
  });

  it("counts planning requests in the header and stops counting when the beta ends", async () => {
    const server = installServer();
    render(<App />);
    expect(await screen.findByText("8 of 8 planning requests left")).toBeTruthy();
    cleanup();
    server.session.pilot_ended = true;
    render(<App />);
    await screen.findByRole("heading", { name: "Your preferences" });
    expect(screen.queryByText(/requests left/)).toBeNull();
  });

  it("says over which days the catalog's prices were checked", async () => {
    const dated = structuredClone(catalog);
    const offers = (object(dated).products as Body[]).flatMap((product) =>
      (product.variants as Body[]).flatMap((variant) => variant.offers as Body[]),
    );
    for (const offer of offers) object(offer.source).checked_at = "2026-10-30T09:00:00Z";
    installServer({ catalog: dated });
    render(<App />);
    expect(await screen.findByText(/products\. Prices checked 30 Oct 2026$/)).toBeTruthy();
    cleanup();
    object(offers[0].source).checked_at = "2026-10-01T09:00:00Z";
    render(<App />);
    expect(await screen.findByText(/products\. Prices checked 1 Oct 2026 to 30 Oct 2026$/)).toBeTruthy();
  });

  it("keeps a failed plan's technical error out of the page", async () => {
    installServer({
      plans: [
        {
          outcome: "generation_failed",
          plan: null,
          conflicts: [],
          violations: [],
          notices: [],
          error: "1 validation error for Plan https://errors.pydantic.dev/2.14/v/too_short",
        },
      ],
    });
    render(<App />);
    await screen.findByText(/products\. Prices checked/);
    fireEvent.change(screen.getByLabelText("What should the looks work for?"), { target: { value: "Office" } });
    fireEvent.change(screen.getByLabelText("Budget in EUR"), { target: { value: "250" } });
    fireEvent.click(screen.getByRole("button", { name: "Generate three looks" }));
    expect(await screen.findByText(/No plan passed the catalog checks/)).toBeTruthy();
    expect(screen.queryByText(/pydantic/)).toBeNull();
  });

  it.each(["provider_error", "provider_timeout", "generation_failed"])(
    "says that a failed %s request still counts against the invite",
    async (outcome) => {
      installServer({
        plans: [{ outcome, plan: null, conflicts: [], violations: [], notices: [], error: null }],
      });
      render(<App />);
      await screen.findByText(/products\. Prices checked/);
      fireEvent.change(screen.getByLabelText("What should the looks work for?"), { target: { value: "Office" } });
      fireEvent.change(screen.getByLabelText("Budget in EUR"), { target: { value: "250" } });
      fireEvent.click(screen.getByRole("button", { name: "Generate three looks" }));
      expect(await screen.findByText(/still counts against your invite/)).toBeTruthy();
    },
  );

  it("does not say that a refused request counts, because no plan could pass and it uses no quota", async () => {
    installServer({
      plans: [
        {
          outcome: "infeasible",
          plan: null,
          conflicts: [{ rule: "budget", detail: "The cheapest complete look costs more" }],
          violations: [],
          notices: [],
          error: null,
        },
      ],
    });
    render(<App />);
    await screen.findByText(/products\. Prices checked/);
    fireEvent.change(screen.getByLabelText("What should the looks work for?"), { target: { value: "Office" } });
    fireEvent.change(screen.getByLabelText("Budget in EUR"), { target: { value: "10" } });
    fireEvent.click(screen.getByRole("button", { name: "Generate three looks" }));
    await screen.findByText(/No plan can meet these constraints/);
    expect(screen.queryByText(/still counts against your invite/)).toBeNull();
  });

  it.each([
    ["closed", { beta: false, invited: false }, "The FullAura beta is closed"],
    ["closed to an open session", { beta: false, invited: true }, "The FullAura beta is closed"],
    ["waiting for an invite", { beta: true, invited: false }, "Open your invite"],
  ])("lets a person read and delete saved plans while the beta is %s", async (_, access, heading) => {
    const line = {
      key: "catalog:one",
      kind: "catalog",
      name: "Saved shirt",
      details: "Size M",
      looks: ["look 1"],
      price_cents: 3990,
      retailer: "Shop",
      url: "https://shop.example/shirt",
      checked_at: "2026-10-30T12:00:00.000Z",
      stock: null,
    };
    localStorage.setItem(
      "fullaura.saved-plans.v1",
      JSON.stringify([
        {
          id: "saved-one",
          saved_at: "2026-10-30T12:00:00.000Z",
          name: "Autumn plan",
          brief: { style_goal: "Autumn", preferred_colors: [], occasion: null, season: null },
          state: { plan: firstPlan },
          catalog_id: object(catalog).id,
          shopping_list: [line],
        },
      ]),
    );
    const server = installServer();
    Object.assign(server.session, access);
    render(<App />);
    expect(await screen.findByText(heading)).toBeTruthy();
    expect(screen.getByText("Autumn plan")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Restore" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Save this plan" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Show list" }));
    expect(within(screen.getByRole("table")).getByText("Saved shirt")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
    await waitFor(() => expect(screen.queryByText("Autumn plan")).toBeNull());
    expect(JSON.parse(localStorage.getItem("fullaura.saved-plans.v1") ?? "[]")).toEqual([]);
  });

  it("shows no saved section on a closed page with nothing saved", async () => {
    const server = installServer();
    Object.assign(server.session, { beta: false, invited: false });
    render(<App />);
    await screen.findByText("The FullAura beta is closed");
    expect(screen.queryByText("Saved on this device")).toBeNull();
  });

  it("opens the closed section that holds an empty required field when the form is submitted", async () => {
    const server = installServer();
    render(<App />);
    await screen.findByText(/products\. Prices checked/);
    fireEvent.change(screen.getByLabelText("What should the looks work for?"), { target: { value: "Office" } });
    fireEvent.change(screen.getByLabelText("Budget in EUR"), { target: { value: "250" } });
    fireEvent.click(screen.getByRole("button", { name: "Add item" }));
    const section = screen.getByText("Items you already own", { selector: "summary span" }).closest("details");
    expect(section?.open).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Generate three looks" }));
    expect(section?.open).toBe(true);
    expect(planRequests(server)).toEqual([]);
    // Like WebKit, jsdom focuses nothing itself, so the field takes the focus.
    await waitFor(() => expect(document.activeElement).toBe(screen.getByLabelText("Color")));
  });

  it("leaves the focus on an invalid field the browser chose over one in a closed section", async () => {
    installServer();
    render(<App />);
    await screen.findByText(/products\. Prices checked/);
    fireEvent.change(screen.getByLabelText("Budget in EUR"), { target: { value: "250" } });
    fireEvent.click(screen.getByRole("button", { name: "Add item" }));
    const goal = screen.getByLabelText("What should the looks work for?");
    goal.focus();
    fireEvent.click(screen.getByRole("button", { name: "Generate three looks" }));
    await act(async () => new Promise((resolve) => setTimeout(resolve)));
    expect(document.activeElement).toBe(goal);
  });

  it("keeps an accepted invite when the catalog fails once, and loads it again on request", async () => {
    const server = installServer({ invited: false, plans: [firstPlan] });
    server.catalogFailures = 1;
    render(<App />);
    fireEvent.change(await screen.findByLabelText("Invite code"), { target: { value: "good-invite" } });
    fireEvent.click(screen.getByRole("button", { name: "Open invite" }));
    expect(await screen.findByText("The catalog could not be loaded")).toBeTruthy();
    expect(screen.queryByText("Loading the catalog")).toBeNull();
    expect(screen.queryByLabelText("Invite code")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Load the catalog again" }));
    await generate();
    const urls = server.requests.map((entry) => entry.url);
    expect(urls.filter((url) => url === "/api/session/invite")).toHaveLength(1);
    expect(urls.filter((url) => url === "/api/catalog")).toHaveLength(2);
  });
});

describe(`${FIXTURE_LABEL}: data notice`, () => {
  it("names what Baseten receives and keeps, with its source, and the owned item in a try-on's text", async () => {
    installServer();
    render(<App />);
    fireEvent.click(await screen.findByText("How FullAura handles your data"));
    const notice = within(screen.getByText("How FullAura handles your data").closest("details") as HTMLElement);
    expect(notice.getByRole("link", { name: "documentation" }).getAttribute("href")).toBe(
      "https://docs.baseten.co/inference/model-apis/overview",
    );
    expect(notice.getByText(/does not store the\s+inputs or outputs of these requests/)).toBeTruthy();
    expect(notice.getByText(/not a check of FullAura's account/)).toBeTruthy();
    expect(notice.getByText(/If the look includes an item you own, the description includes the words/)).toBeTruthy();
    expect(notice.queryByText(/Design my look/)).toBeNull(); // nothing about a mode this invite cannot use
  });

  it("says what Design my look sends only while it is open, and always that a try-on uses no web search", async () => {
    installServer({ design: true });
    render(<App />);
    fireEvent.click(await screen.findByText("How FullAura handles your data"));
    const notice = within(screen.getByText("How FullAura handles your data").closest("details") as HTMLElement);
    expect(
      notice.getByText(
        /not a check of FullAura's account\. Design my look first sends your style goal, colors, occasion, season, budget, clothing line, the kinds of bottoms and dresses you wear, and your shoe and material choices, without your sizes or owned items; it then sends its concept with the catalog products in your sizes, so that the model can pick the closest ones\. FullAura does not store your preferences; the server keeps your latest plan and your latest concept in memory until a day after you last use FullAura\./,
      ),
    ).toBeTruthy();
    expect(notice.getByText(/a description of the look, or of your concept in Design my look, to Meta's Muse Image model/)).toBeTruthy();
    expect(
      notice.getByText(/FullAura turns off the image model's own web and image search, so it works only from your photos and that text\./),
    ).toBeTruthy();
  });
});

describe(`${FIXTURE_LABEL}: planning`, () => {
  it("sends preferences, closed shoes and an operation ID, never a plan", async () => {
    const server = installServer({ plans: [firstPlan] });
    render(<App />);
    fireEvent.change(await screen.findByLabelText("Occasion"), { target: { value: "office" } });
    fireEvent.change(screen.getByLabelText("Season"), { target: { value: "autumn" } });
    await generate();

    const [request] = planRequests(server);
    expect(object(request.brief)).toMatchObject({ occasion: "office", season: "autumn" });
    expect(object(request.state)).toMatchObject({ footwear: "closed", locks: [] });
    expect("plan" in object(request.state)).toBe(false);
    expect(request.plan_token).toBeNull();
    expect(server.requests.find((entry) => entry.url === "/api/plans")?.headers["x-operation-id"]).toMatch(
      /^[0-9a-f-]{36}$/,
    );
    expect(screen.getByRole("heading", { name: "look 3" })).toBeTruthy();
  });

  it("updates with the plan token and the kept items, and keeps the plan when an update fails", async () => {
    const server = installServer({
      plans: [firstPlan, { outcome: "provider_timeout", plan: null, conflicts: [], violations: [], notices: [], error: null }],
    });
    render(<App />);
    await generate();
    fireEvent.click(screen.getByRole("button", { name: "Keep MAARKOS HEAVY WEIGHT T-SHIRT in look 1" }));
    fireEvent.change(screen.getByLabelText("Budget in EUR"), { target: { value: "160" } });
    fireEvent.click(screen.getByRole("button", { name: "Update three looks" }));

    expect(await screen.findByText(/The planning request timed out/)).toBeTruthy();
    expect(screen.getByText(/The looks below are from the last successful request/)).toBeTruthy();
    const second = planRequests(server)[1];
    expect(second.plan_token).toBe("token-1");
    expect(object(second.state).locks).toEqual([
      { look_id: "look_1", slot: "top", item: { kind: "catalog", id: "ae-maarkos-moonshadow-m" } },
    ]);
    expect(screen.getAllByRole("heading", { name: "MAARKOS HEAVY WEIGHT T-SHIRT" })).toHaveLength(2);
  });

  it("replaces one item and keeps every other position", async () => {
    const server = installServer({ plans: [firstPlan, replacementPlan] });
    render(<App />);
    await generate();
    fireEvent.click(screen.getByRole("button", { name: "Replace MAARKOS HEAVY WEIGHT T-SHIRT in look 1" }));
    expect(await screen.findByText(/The item was replaced/)).toBeTruthy();
    const replacement = planRequests(server)[1];
    expect(replacement.replacement).toEqual({
      look_id: "look_1",
      slot: "top",
      item: { kind: "catalog", id: "ae-maarkos-moonshadow-m" },
    });
    expect(object(replacement.state).locks).toHaveLength(5);
  });

  it("restores an expired plan on the server and repeats the update once", async () => {
    const server = installServer({ plans: [firstPlan, reducedBudgetPlan] });
    render(<App />);
    await generate();
    server.expireToken();
    fireEvent.change(screen.getByLabelText("Budget in EUR"), { target: { value: "160" } });
    fireEvent.click(screen.getByRole("button", { name: "Update three looks" }));
    expect(await screen.findAllByText("REHMAA LEGGINGS")).not.toHaveLength(0);
    const urls = server.requests.map((entry) => entry.url).filter((url) => url.startsWith("/api/plans"));
    expect(urls).toEqual(["/api/plans", "/api/plans", "/api/plans/restore", "/api/plans"]);
  });

  it("clears a plan the server can no longer restore, so the next request starts afresh", async () => {
    const server = installServer({ plans: [firstPlan, reducedBudgetPlan] });
    render(<App />);
    await generate();
    server.expireToken();
    server.restoreFails = true;
    fireEvent.click(screen.getByRole("button", { name: "Update three looks" }));
    expect(await screen.findByText(/Your plan expired on the server/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Generate three looks" }));
    await screen.findByText(/The three looks passed the catalog checks/);
    const last = planRequests(server).at(-1);
    expect(last?.plan_token).toBeNull();
    expect(object(last?.state).locks).toEqual([]);
  });

  it("keeps no items of a working plan the server refused on reload, so the next update sends none", async () => {
    storeWorkingPlan(reducedBudgetPlan, [
      { look_id: "look_1", slot: "top", item: { kind: "catalog", id: "ae-maarkos-moonshadow-m" } },
      { look_id: "look_1", slot: "bottom", item: { kind: "catalog", id: "ae-rehmaa-velvet-red-m" } },
    ]);
    // The update's plan has neither item at the position it was kept in.
    const server = installServer({ plans: [firstPlan, replacementPlan] });
    server.restoreFails = true;
    render(<App />);
    expect(await screen.findByText(/Your last plan no longer passes today's checks/)).toBeTruthy();
    expect(sessionStorage.getItem(CURRENT_PLAN_KEY)).toBeNull();

    server.restoreFails = false;
    await generate();
    // The new plan has the T-shirt in look 1 again, but nobody kept it in this plan.
    expect(screen.queryAllByRole("button", { pressed: true })).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "Update three looks" }));
    const tShirts = () => screen.getAllByRole("heading", { name: "MAARKOS HEAVY WEIGHT T-SHIRT" });
    await waitFor(() => expect(tShirts()).toHaveLength(1)); // only look 3 has it now
    expect(object(planRequests(server)[1].state).locks).toEqual([]);

    // The server checks the kept items of the tab's plan against the plan, so it restores only
    // when none was left over from the refused plan.
    cleanup();
    render(<App />);
    expect(await screen.findByRole("heading", { name: "look 3" })).toBeTruthy();
    expect(tShirts()).toHaveLength(1);
    expect(screen.queryByText(/no longer passes today's checks/)).toBeNull();
  });

  it("holds new requests while a reload brings back the working plan, so a slow answer replaces nothing", async () => {
    storeWorkingPlan(firstPlan);
    const server = installServer({ plans: [reducedBudgetPlan] });
    const slow = deferred();
    server.restoreGate = slow.promise;
    render(<App />);
    expect(await screen.findByText("Checking your last plan against today's catalog.")).toBeTruthy();
    const submit = screen.getByRole("button", { name: "Planning looks…" }) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
    fireEvent.click(submit);
    expect(planRequests(server)).toHaveLength(0);

    await act(async () => slow.resolve());
    expect(await screen.findByRole("heading", { name: "look 3" })).toBeTruthy();
    expect(screen.queryByText("Checking your last plan against today's catalog.")).toBeNull();
    expect((screen.getByRole("button", { name: "Update three looks" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("keeps the working plan when a reload's restore loses the connection, so the next reload restores it", async () => {
    const stored = storeWorkingPlan(firstPlan);
    const server = installServer();
    server.restoreOffline = true;
    render(<App />);
    expect(await screen.findByText("The request could not be completed. Check your connection and try again.")).toBeTruthy();
    expect(sessionStorage.getItem(CURRENT_PLAN_KEY)).toBe(stored);

    server.restoreOffline = false;
    cleanup();
    render(<App />);
    expect(await screen.findByRole("heading", { name: "look 3" })).toBeTruthy();
  });

  it("keeps an expired plan whose restore lost the connection, and says so instead of calling it cleared", async () => {
    const server = installServer({ plans: [firstPlan, reducedBudgetPlan] });
    render(<App />);
    await generate();
    server.expireToken();
    server.restoreOffline = true;
    fireEvent.click(screen.getByRole("button", { name: "Update three looks" }));
    expect(await screen.findByText(/^The request could not be completed\. Check your connection and try again\./)).toBeTruthy();
    expect(screen.getByText(/The looks below are from the last successful request/)).toBeTruthy();
    expect(screen.queryByText(/Your plan expired on the server/)).toBeNull();

    server.restoreOffline = false;
    fireEvent.click(screen.getByRole("button", { name: "Update three looks" }));
    expect(await screen.findAllByText("REHMAA LEGGINGS")).not.toHaveLength(0);
  });

  it("says next to the button what the budget limits, and totals the plan as the shopping list does", async () => {
    installServer({ plans: [firstPlan] });
    render(<App />);
    await screen.findByText(/products\. Prices checked/);
    const button = screen.getByRole("button", { name: "Generate three looks" });
    const meaning = () => document.getElementById(button.getAttribute("aria-describedby") ?? "")?.textContent ?? "";
    expect(meaning()).toMatch(/^The budget is a limit for each look on its own, so the three looks together can cost more\./);
    fireEvent.change(screen.getByLabelText("Budget in EUR"), { target: { value: "150" } });
    expect(meaning()).toMatch(/150,00 is the limit for each look on its own, so the three looks together can cost up to €\s450,00\./);
    expect(meaning()).toMatch(/without delivery or returns/);
    fireEvent.click(screen.getByRole("radio", { name: "All three looks together" }));
    expect(meaning()).toMatch(/150,00 is the limit for all three looks together, counting an item used in more than one look once/);
    fireEvent.click(screen.getByRole("radio", { name: "Each look" }));

    await generate();
    const heading = screen.getByText(/Items total, each once/).closest("p") as HTMLElement;
    const listTotal = screen.getByText(/^Items total$/).closest("p") as HTMLElement;
    expect(heading.querySelector("strong")?.textContent).toBe(listTotal.querySelector("strong")?.textContent);
    expect(heading.textContent).toMatch(/Each look within €\s250,00/);
  });

  it("pauses planning when spending cannot be recorded", async () => {
    installServer({ accounting: false });
    render(<App />);
    expect(await screen.findByText(/planning is paused/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Generate three looks" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("keeps the plan on the screen when the store fails during a session", async () => {
    const server = installServer({ plans: [firstPlan] });
    render(<App />);
    await generate();
    // What the server sends while the store cannot be read.
    Object.assign(server.session, {
      accounting: false,
      age_confirmed: false,
      photo_consent: false,
      planning_open: false,
      previews_open: false,
    });
    fireEvent.click(screen.getByRole("button", { name: "Update three looks" }));
    expect(await screen.findByText(/planning is paused/)).toBeTruthy();
    expect(screen.queryByText("Before you start")).toBeNull();
    expect(screen.getAllByRole("heading", { name: "MAARKOS HEAVY WEIGHT T-SHIRT" })).toHaveLength(2);
    expect((screen.getByRole("button", { name: "Save this plan" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("asks for the age once the store is back, for a person who never confirmed it", async () => {
    const server = installServer({ accounting: false });
    render(<App />);
    await screen.findByText(/planning is paused/);
    cleanup();
    Object.assign(server.session, { accounting: true, planning_open: true });
    render(<App />);
    expect(await screen.findByText("Before you start")).toBeTruthy();
  });

  it("names products in a failure instead of catalog IDs and position codes", async () => {
    installServer({
      plans: [
        {
          outcome: "generation_failed",
          plan: null,
          conflicts: [],
          violations: [
            { rule: "fit", detail: "ae-maarkos-moonshadow-m is letter size M, not L" },
            { rule: "budget", detail: "EUR 124.88 for look look_1, over the EUR 100.00 budget for each look" },
            { rule: "unknown_item", detail: "No catalog record removed-product-m" },
          ],
          notices: [],
          error: null,
        },
      ],
    });
    render(<App />);
    await screen.findByText(/products\. Prices checked/);
    fireEvent.change(screen.getByLabelText("What should the looks work for?"), { target: { value: "Office" } });
    fireEvent.change(screen.getByLabelText("Budget in EUR"), { target: { value: "100" } });
    fireEvent.click(screen.getByRole("button", { name: "Generate three looks" }));
    const message = await screen.findByText(/No plan passed the catalog checks/);
    expect(message.textContent).toContain("MAARKOS HEAVY WEIGHT T-SHIRT is letter size M, not L");
    expect(message.textContent).toContain("for look 1, over the");
    expect(message.textContent).toContain("An item in this plan is no longer in the catalog.");
    expect(message.textContent).not.toMatch(/ae-maarkos|look_1|removed-product/);
  });

  it("asks for a style goal made of more than spaces before sending a request", async () => {
    const server = installServer({ plans: [firstPlan] });
    render(<App />);
    await screen.findByText(/products\. Prices checked/);
    fireEvent.change(screen.getByLabelText("What should the looks work for?"), { target: { value: "   " } });
    fireEvent.change(screen.getByLabelText("Budget in EUR"), { target: { value: "250" } });
    fireEvent.click(screen.getByRole("button", { name: "Generate three looks" }));
    expect(await screen.findAllByText("Describe what the looks should work for.")).not.toHaveLength(0);
    expect(planRequests(server)).toHaveLength(0);
  });

  it("moves the keyboard to Add item when an owned item is removed", async () => {
    installServer();
    render(<App />);
    await screen.findByText(/products\. Prices checked/);
    const add = screen.getByRole("button", { name: "Add item" });
    for (let count = 0; count < 3; count += 1) fireEvent.click(add);
    expect((add as HTMLButtonElement).disabled).toBe(true);
    const remove = screen.getByRole("button", { name: "Remove item 2" });
    remove.focus();
    fireEvent.click(remove);
    expect(document.activeElement).toBe(add);
  });

  it("does not let an owned item removed from the form be kept, so the next update stays valid", async () => {
    const withOwned = object(JSON.parse(firstPlanJson));
    object((withOwned.looks as Body[])[0].items).accessory = { kind: "owned", id: "owned-1" };
    const server = installServer({ plans: [withOwned, firstPlan] });
    render(<App />);
    await screen.findByText(/products\. Prices checked/);
    fireEvent.click(screen.getByRole("button", { name: "Add item" }));
    fireEvent.change(screen.getByLabelText("Color"), { target: { value: "black" } });
    fireEvent.change(screen.getByLabelText("Description"), { target: { value: "leather belt" } });
    await generate();
    const keep = () => screen.getByRole("button", { name: "Keep leather belt in look 1" }) as HTMLButtonElement;
    expect(keep().disabled).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Remove item 1" }));
    expect(keep().disabled).toBe(true);
    expect(screen.getByText("You removed this item from your items, so it cannot be kept.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Update three looks" }));
    await waitFor(() => expect(planRequests(server)).toHaveLength(2));
    const state = object(planRequests(server)[1].state);
    expect(state.owned_items).toEqual([]);
    expect((state.locks as Body[]).some((lock) => object(lock.item).kind === "owned")).toBe(false);
  });
});

describe(`${FIXTURE_LABEL}: shopping list and saved plans`, () => {
  const KEPT = { look_id: "look_1", slot: "top", item: { kind: "catalog", id: "ae-maarkos-moonshadow-m" } };
  const keepTShirt = () =>
    fireEvent.click(screen.getByRole("button", { name: "Keep MAARKOS HEAVY WEIGHT T-SHIRT in look 1" }));
  const releaseTShirt = () => screen.findByRole("button", { name: "Release MAARKOS HEAVY WEIGHT T-SHIRT in look 1" });

  it("keeps a kept item through saving and restoring", async () => {
    installServer({ plans: [firstPlan] });
    render(<App />);
    await generate();
    keepTShirt();
    fireEvent.click(screen.getByRole("button", { name: "Save this plan" }));
    await screen.findByText(/Saved "Relaxed office outfits" on this device/);
    expect(JSON.parse(localStorage.getItem("fullaura.saved-plans.v1") ?? "[]")[0].state.locks).toEqual([KEPT]);

    fireEvent.click(await releaseTShirt());
    fireEvent.click(screen.getByRole("button", { name: "Restore" }));
    fireEvent.click(screen.getByRole("button", { name: "Replace current plan" }));
    await screen.findByText("The saved plan still passes the catalog checks.");
    expect(await releaseTShirt()).toBeTruthy();
  });

  const restoresOf = (server: Server) => server.requests.filter((entry) => entry.url === "/api/plans/restore").length;

  it("asks before a restore when kept items changed since the plan was saved", async () => {
    const server = installServer({ plans: [firstPlan] });
    render(<App />);
    await generate();
    fireEvent.click(screen.getByRole("button", { name: "Save this plan" }));
    await screen.findByText(/Saved "Relaxed office outfits" on this device/);
    keepTShirt();
    fireEvent.click(screen.getByRole("button", { name: "Restore" }));
    expect(screen.getByRole("alert").textContent).toMatch(/kept items are not saved/);
    expect(restoresOf(server)).toBe(0);
  });

  it("asks before a restore when the form changed since the plan was saved", async () => {
    const server = installServer({ plans: [firstPlan] });
    render(<App />);
    await generate();
    fireEvent.click(screen.getByRole("button", { name: "Save this plan" }));
    await screen.findByText(/Saved "Relaxed office outfits" on this device/);
    fireEvent.change(screen.getByLabelText("Budget in EUR"), { target: { value: "249" } });
    fireEvent.click(screen.getByRole("button", { name: "Restore" }));
    expect(screen.getByRole("alert").textContent).toMatch(/changes to the preferences are not applied/);
    expect(restoresOf(server)).toBe(0);
    fireEvent.click(screen.getByRole("button", { name: "Replace current plan" }));
    await screen.findByText(/^Restored "Relaxed office outfits"/);
    expect((screen.getByLabelText("Budget in EUR") as HTMLInputElement).value).toBe("250");
  });

  it("asks before a restore replaces preferences typed with no plan yet, and not on an untouched form", async () => {
    const server = installServer({ plans: [firstPlan] });
    render(<App />);
    await generate();
    fireEvent.click(screen.getByRole("button", { name: "Save this plan" }));
    await screen.findByText(/Saved "Relaxed office outfits" on this device/);
    cleanup();
    sessionStorage.clear(); // a new tab, with the saved plan and no working plan
    render(<App />);
    await screen.findByText(/products\. Prices checked/);
    const goal = screen.getByLabelText("What should the looks work for?");
    fireEvent.change(goal, { target: { value: "A long answer I typed" } });
    fireEvent.click(await screen.findByRole("button", { name: "Restore" }));
    expect(screen.getByRole("alert").textContent).toMatch(/changes to the preferences/);
    expect(restoresOf(server)).toBe(0);
    fireEvent.click(screen.getByRole("button", { name: "Keep current plan" }));
    fireEvent.change(goal, { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Restore" }));
    await screen.findByText(/^Restored "Relaxed office outfits"/);
    expect(restoresOf(server)).toBe(1);
  });

  it("does not ask when the page already holds the saved plan, also after restoring it", async () => {
    const server = installServer({ plans: [firstPlan] });
    render(<App />);
    await generate();
    keepTShirt();
    fireEvent.click(screen.getByRole("button", { name: "Save this plan" }));
    await screen.findByText(/Saved "Relaxed office outfits" on this device/);
    fireEvent.click(screen.getByRole("button", { name: "Restore" }));
    await screen.findByText(/^Restored "Relaxed office outfits"/);
    expect(screen.queryByRole("button", { name: "Keep current plan" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Restore" }));
    await waitFor(() => expect(restoresOf(server)).toBe(2));
    expect(screen.queryByRole("button", { name: "Keep current plan" })).toBeNull();
  });

  it("does not bring the question back by itself once what it warned about was saved", async () => {
    installServer({ plans: [firstPlan] });
    render(<App />);
    await generate();
    fireEvent.click(screen.getByRole("button", { name: "Save this plan" }));
    await screen.findByText(/Saved "Relaxed office outfits" on this device/);
    keepTShirt();
    fireEvent.click(screen.getByRole("button", { name: "Restore" }));
    expect(screen.getByRole("button", { name: "Keep current plan" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Save this plan" }));
    expect(screen.queryByRole("button", { name: "Keep current plan" })).toBeNull();
    const release = await releaseTShirt();
    release.focus();
    fireEvent.click(release); // differs from the saved copy again
    expect(screen.queryByRole("button", { name: "Keep current plan" })).toBeNull();
    expect(document.activeElement?.getAttribute("aria-label")).toMatch(/MAARKOS/);
  });

  it("asks before a restore replaces a plan that is not saved, and answers beside the saved plans", async () => {
    const server = installServer({ plans: [firstPlan, reducedBudgetPlan] });
    const restores = () => server.requests.filter((entry) => entry.url === "/api/plans/restore").length;
    render(<App />);
    await generate();
    fireEvent.click(screen.getByRole("button", { name: "Save this plan" }));
    await screen.findByText(/Saved "Relaxed office outfits" on this device/);
    fireEvent.change(screen.getByLabelText("Budget in EUR"), { target: { value: "160" } });
    fireEvent.click(screen.getByRole("button", { name: "Update three looks" }));
    await screen.findAllByText("REHMAA LEGGINGS");
    expect(screen.queryByText(/^Saved "Relaxed office outfits"/)).toBeNull(); // this plan is not the saved one

    fireEvent.click(screen.getByRole("button", { name: "Restore" }));
    expect(screen.getByRole("alert").textContent).toMatch(/not saved/);
    expect(restores()).toBe(0);
    const keepCurrent = screen.getByRole("button", { name: "Keep current plan" });
    expect(document.activeElement).toBe(keepCurrent); // the safe choice comes first
    fireEvent.click(keepCurrent);
    expect(screen.queryByRole("button", { name: "Replace current plan" })).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Restore" }));
    expect(restores()).toBe(0);
    expect(screen.getAllByText("REHMAA LEGGINGS").length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("button", { name: "Restore" }));
    fireEvent.click(screen.getByRole("button", { name: "Replace current plan" }));
    const message = await screen.findByText(/^Restored "Relaxed office outfits"/);
    expect(document.activeElement).toBe(message);
    expect(restores()).toBe(1);
    expect(screen.queryByText(/^Saved "Relaxed office outfits"/)).toBeNull();
    expect(screen.queryByText("REHMAA LEGGINGS")).toBeNull();
  });

  it("says beside the saved plans when a saved plan cannot be restored", async () => {
    const server = installServer({ plans: [firstPlan] });
    render(<App />);
    await generate();
    fireEvent.click(screen.getByRole("button", { name: "Save this plan" }));
    await screen.findByText(/Saved "Relaxed office outfits" on this device/);
    server.restoreFails = true;
    fireEvent.click(screen.getByRole("button", { name: "Restore" }));
    expect(await screen.findByText(/^"Relaxed office outfits" was not restored/)).toBeTruthy();
  });

  it("keeps a kept item through a reload, and forgets it once released", async () => {
    installServer({ plans: [firstPlan] });
    render(<App />);
    await generate();
    keepTShirt();
    cleanup();
    render(<App />);
    fireEvent.click(await releaseTShirt());
    cleanup();
    render(<App />);
    expect(await screen.findByRole("button", { name: "Keep MAARKOS HEAVY WEIGHT T-SHIRT in look 1" })).toBeTruthy();
  });

  it("keeps a kept item when an expired plan is restored for an update", async () => {
    const server = installServer({ plans: [firstPlan, firstPlan] });
    render(<App />);
    await generate();
    keepTShirt();
    server.expireToken();
    fireEvent.click(screen.getByRole("button", { name: "Update three looks" }));
    await screen.findByText(/The three looks passed the catalog checks/);
    const restore = server.requests.find((entry) => entry.url === "/api/plans/restore");
    expect(object(object(restore?.body).state).locks).toEqual([KEPT]);
    expect(await releaseTShirt()).toBeTruthy();
  });

  it("lists each product once and copies the list", async () => {
    const writeText = vi.fn<(text: string) => Promise<void>>(async () => undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    installServer({ plans: [firstPlan] });
    render(<App />);
    await generate();
    const table = within(screen.getByRole("table"));
    expect(table.getAllByText("MAARKOS HEAVY WEIGHT T-SHIRT")).toHaveLength(1);
    expect(table.getByText("look 1, look 3")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Copy list" }));
    await screen.findByText("Copied the list.");
    const text = String(writeText.mock.calls[0]?.[0]);
    expect(text.match(/MAARKOS HEAVY WEIGHT T-SHIRT/g)).toHaveLength(1);
    expect(text).toMatch(/Delivery and returns are not included/);
  });

  it("saves a plan without photos, restores it through a check and deletes it after confirmation", async () => {
    const server = installServer({ plans: [firstPlan] });
    render(<App />);
    await generate();
    fireEvent.click(screen.getByRole("button", { name: "Save this plan" }));
    await screen.findByText(/Saved "Relaxed office outfits" on this device/);
    const stored = localStorage.getItem("fullaura.saved-plans.v1") ?? "";
    expect(stored).toContain("ae-maarkos-moonshadow-m");
    expect(stored).not.toMatch(/blob:|image\/|photo/i);

    fireEvent.click(screen.getByRole("button", { name: "Restore" }));
    expect(await screen.findByText("The saved plan still passes the catalog checks.")).toBeTruthy();
    const restore = server.requests.find((entry) => entry.url === "/api/plans/restore");
    expect(object(object(restore?.body).state).plan).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
    await screen.findByText("Deleted from this device.");
    expect(JSON.parse(localStorage.getItem("fullaura.saved-plans.v1") ?? "[]")).toEqual([]);
  });

  it("names the saved plan that a new one pushes out", async () => {
    const older = Array.from({ length: 10 }, (_, index) => ({
      id: `saved-${index}`,
      saved_at: "2026-10-30T12:00:00.000Z",
      name: index === 9 ? "The oldest plan" : `Plan ${index}`,
      brief: { style_goal: "Older plan", preferred_colors: [], occasion: null, season: null },
      state: { plan: firstPlan },
      catalog_id: object(catalog).id,
      shopping_list: [],
    }));
    localStorage.setItem("fullaura.saved-plans.v1", JSON.stringify(older));
    installServer({ plans: [firstPlan] });
    render(<App />);
    await generate();
    fireEvent.click(screen.getByRole("button", { name: "Save this plan" }));
    expect(await screen.findByText(/It keeps 10 plans, so "The oldest plan" was removed/)).toBeTruthy();
    expect(JSON.parse(localStorage.getItem("fullaura.saved-plans.v1") ?? "[]")).toHaveLength(10);

    fireEvent.click(screen.getByRole("button", { name: "Save this plan" })); // the same plan again
    await screen.findByText(/^Saved "Relaxed office outfits" on this device\.$/);
  });

  it("shows stock in the shopping list when the shop reports it", async () => {
    const writeText = vi.fn<(text: string) => Promise<void>>(async () => undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    const stocked = structuredClone(catalog);
    const shirt = (object(stocked).products as Body[])
      .flatMap((product) => product.variants as Body[])
      .find((variant) => variant.id === "ae-maarkos-moonshadow-m");
    for (const offer of (shirt?.offers ?? []) as Body[]) offer.stock = "in_stock";
    installServer({ plans: [firstPlan], catalog: stocked });
    render(<App />);
    await generate();
    const row = within(screen.getByRole("table")).getByText("MAARKOS HEAVY WEIGHT T-SHIRT").closest("tr") as HTMLElement;
    expect(within(row).getByText("In stock")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Copy list" }));
    await screen.findByText("Copied the list.");
    const line = String(writeText.mock.calls[0]?.[0]).split("\n").find((text) => text.includes("MAARKOS"));
    expect(line).toContain("In stock");
  });

  it("keeps the keyboard in the saved plans when one is deleted", async () => {
    installServer({ plans: [firstPlan] });
    render(<App />);
    await generate();
    fireEvent.click(screen.getByRole("button", { name: "Save this plan" }));
    await screen.findByText(/Saved "Relaxed office outfits"/);
    const remove = screen.getByRole("button", { name: "Delete" });
    remove.focus();
    fireEvent.click(remove);
    // The safe answer takes the focus, so pressing the same key again deletes nothing.
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Keep it" }));
    fireEvent.click(document.activeElement as HTMLElement);
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Delete" }));
    expect(screen.getByRole("button", { name: "Show list" })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
    expect(document.activeElement).toBe(screen.getByText("Deleted from this device."));
  });

  it("names the plan's list and an opened saved list each by its own title", async () => {
    installServer({ plans: [firstPlan] });
    render(<App />);
    await generate();
    fireEvent.click(screen.getByRole("button", { name: "Save this plan" }));
    await screen.findByText(/Saved "Relaxed office outfits"/);
    fireEvent.click(screen.getByRole("button", { name: "Show list" }));
    expect(screen.getByRole("region", { name: "Saved list: Relaxed office outfits" })).toBeTruthy();
    expect(screen.getByRole("region", { name: "Shopping list" })).toBeTruthy();
  });

  it("restores a saved plan only once the catalog has loaded, so a restore the server accepts is shown", async () => {
    const server = installServer({ plans: [firstPlan] });
    render(<App />);
    await generate();
    fireEvent.click(screen.getByRole("button", { name: "Save this plan" }));
    await screen.findByText(/Saved "Relaxed office outfits"/);
    cleanup();
    sessionStorage.clear();
    server.catalogFailures = 1;
    render(<App />);
    await screen.findByText("The catalog could not be loaded");
    expect((screen.getByRole("button", { name: "Restore" }) as HTMLButtonElement).disabled).toBe(true);
    expect(server.requests.some((entry) => entry.url === "/api/plans/restore")).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "Load the catalog again" }));
    await screen.findByText(/products\. Prices checked/);
    fireEvent.click(screen.getByRole("button", { name: "Restore" }));
    expect(await screen.findByText(/^Restored "Relaxed office outfits"/)).toBeTruthy();
    expect(screen.getByRole("heading", { name: "look 3" })).toBeTruthy();
  });
});

describe(`${FIXTURE_LABEL}: product photos`, () => {
  /** The real catalog has no photo FullAura may show, so this adds an invented one. */
  function catalogWithPhoto(): unknown {
    const copy = object(structuredClone(catalog));
    const variants = (copy.products as Body[]).flatMap((product) => product.variants as Body[]);
    const variant = variants.find((entry) => entry.id === "ae-maarkos-moonshadow-m");
    if (!variant) throw new Error("The fixture variant is missing");
    variant.image = {
      url: "https://images.synthetic.example/maarkos-grey.jpg",
      alt: "Synthetic test photo of a grey T-shirt",
      width: 600,
      height: 800,
      attribution: "Synthetic test image",
    };
    return copy;
  }

  it("shows a variant's photo in a sized box with its description, and keeps the box if it fails", async () => {
    installServer({ plans: [firstPlan], catalog: catalogWithPhoto() });
    render(<App />);
    await generate();
    const photos = screen.getAllByRole("img", { name: "Synthetic test photo of a grey T-shirt" });
    expect(photos).toHaveLength(2); // the T-shirt is in look 1 and look 3
    expect([photos[0].getAttribute("width"), photos[0].getAttribute("height")]).toEqual(["600", "800"]);
    expect(photos[0].getAttribute("loading")).toBe("lazy");
    expect(photos[0].getAttribute("referrerpolicy")).toBe("no-referrer"); // the host sees no page URL
    expect(screen.getAllByText("Synthetic test image")).toHaveLength(2);
    expect(screen.getAllByRole("img")).toHaveLength(2); // items without a photo show none

    fireEvent.error(photos[0]);
    expect(screen.getByRole("img", { name: "Synthetic test photo of a grey T-shirt: image unavailable" })).toBeTruthy();
    const item = screen.getByText("Image unavailable").closest("li") as HTMLElement;
    expect(within(item).getByRole("link", { name: "Armedangels" }).getAttribute("href")).toMatch(/^https:\/\//);
    expect(within(item).getByText(/^€/)).toBeTruthy();
  });

  it("refuses a photo that is not served over HTTPS", async () => {
    const copy = object(catalogWithPhoto());
    const variant = (copy.products as Body[])
      .flatMap((product) => product.variants as Body[])
      .find((entry) => entry.id === "ae-maarkos-moonshadow-m");
    object(variant?.image).url = "http://images.synthetic.example/maarkos-grey.jpg";
    installServer({ catalog: copy });
    render(<App />);
    expect(await screen.findByText("The catalog could not be loaded")).toBeTruthy();
  });
});

describe(`${FIXTURE_LABEL}: what the checks and buttons tell a person`, () => {
  /* The first plan holds three products in letter sizes and two in waist and length; the catalog's
   * shops report no stock for four of the five. */
  it("does not claim a size or stock it could not check, and names them", async () => {
    installServer({ plans: [firstPlan] });
    render(<App />);
    await screen.findByText(/products\. Prices checked/);
    fireEvent.change(screen.getByLabelText("What should the looks work for?"), { target: { value: "Office" } });
    fireEvent.change(screen.getByLabelText("Budget in EUR"), { target: { value: "250" } });
    fireEvent.change(screen.getByLabelText("Waist sizes"), { target: { value: "50" } });
    fireEvent.click(screen.getByRole("button", { name: "Generate three looks" }));
    const passed = await screen.findByText(/The three looks passed the catalog checks/);
    expect(passed.textContent).not.toMatch(/sizes|stock/);
    const note = screen.getByText(/Sizes not checked/);
    expect(note.textContent).toContain("letter sizes on 3 items");
    expect(note.textContent).toContain("does not convert");
    expect(note.textContent).toContain("Stock not reported for 4 items");
    expect(note.textContent).not.toContain("waist and length on");
    // Each product in a letter size says so where it is shown; MAARKOS is in two looks.
    expect(screen.getAllByText("Size not checked against yours")).toHaveLength(4);
    expect(within(screen.getByRole("table")).getAllByText(/size not checked/)).toHaveLength(3);
  });

  it("keeps naming what it did not check after a reload", async () => {
    installServer({ plans: [firstPlan] });
    render(<App />);
    await generate();
    cleanup();
    render(<App />);
    expect(await screen.findByText(/Sizes not checked/)).toBeTruthy();
  });

  it("explains in each look why Replace is off once preferences changed, and takes it back", async () => {
    installServer({ plans: [firstPlan] });
    render(<App />);
    await generate();
    const replace = () => screen.getAllByRole("button", { name: /^Replace .* in look 1$/ })[0] as HTMLButtonElement;
    expect(replace().disabled).toBe(false);
    fireEvent.change(screen.getByLabelText("Budget in EUR"), { target: { value: "249" } });
    expect(replace().disabled).toBe(true);
    expect(screen.getAllByText(/Replace is off until you select "Update three looks"/)).toHaveLength(3);
    fireEvent.change(screen.getByLabelText("Budget in EUR"), { target: { value: "250" } });
    expect(replace().disabled).toBe(false);
    expect(screen.queryByText(/Replace is off until/)).toBeNull();
  });

  it("explains why Replace is off after a reload too, when no request has run on the page", async () => {
    installServer();
    storeWorkingPlan(firstPlan);
    render(<App />);
    await screen.findAllByRole("button", { name: /^Replace .* in look 1$/ });
    fireEvent.change(screen.getByLabelText("Budget in EUR"), { target: { value: "249" } });
    expect(screen.getAllByText(/Replace is off until you select "Update three looks"/)).toHaveLength(3);
  });

  it("does not point to Update when Update cannot be used, as once the requests are used up", async () => {
    const server = installServer();
    server.session.planning_open = false;
    storeWorkingPlan(firstPlan);
    render(<App />);
    await screen.findAllByRole("button", { name: /^Replace .* in look 1$/ });
    fireEvent.change(screen.getByLabelText("Budget in EUR"), { target: { value: "249" } });
    expect(screen.queryByText(/Replace is off until/)).toBeNull();
  });
});

describe(`${FIXTURE_LABEL}: try-on`, () => {
  async function prepareTryOn(server: Server) {
    render(<App />);
    await generate();
    fireEvent.click(screen.getByRole("button", { name: "I agree to this processing of my photos" }));
    await screen.findByText("You agreed to this processing.");
    fireEvent.change(screen.getByLabelText("Portrait photo"), {
      target: { files: [new File(["p"], "portrait.png", { type: "image/png" })] },
    });
    fireEvent.change(screen.getByLabelText("Full-body photo"), {
      target: { files: [new File(["f"], "full.webp", { type: "image/webp" })] },
    });
    return server;
  }

  it("needs consent, sends two redrawn photos with the plan token and look, and labels the result", async () => {
    const server = await prepareTryOn(installServer({ plans: [firstPlan] }));
    fireEvent.click(screen.getByRole("radio", { name: "look 2" }));
    fireEvent.click(await screen.findByRole("button", { name: "Try on look 2" }));
    expect(await screen.findByRole("img", { name: "Experimental try-on of look 2" })).toBeTruthy();
    expect(screen.getByText(/Experimental visualization/)).toBeTruthy();
    const request = server.requests.find((entry) => entry.url === "/api/previews");
    expect(request?.headers).toMatchObject({ "x-plan-token": "token-1", "x-look-id": "look_2" });
    const form = request?.body as FormData;
    expect([...form.keys()].sort()).toEqual(["full_body", "portrait"]);
    expect((form.get("portrait") as File).type).toBe("image/jpeg");
  });

  it("sends the newest photo chosen for an input, even when an earlier choice finishes preparing later", async () => {
    const server = await prepareTryOn(installServer({ plans: [firstPlan] }));
    await waitFor(() =>
      expect((screen.getByRole("button", { name: "Try on look 1" }) as HTMLButtonElement).disabled).toBe(false),
    );
    const earlier = deferred<Blob>();
    const newer = deferred<Blob>();
    vi.mocked(normalizePhoto).mockImplementationOnce(() => earlier.promise).mockImplementationOnce(() => newer.promise);
    const portrait = screen.getByLabelText("Portrait photo");
    fireEvent.change(portrait, { target: { files: [new File(["a"], "someone-else.png", { type: "image/png" })] } });
    fireEvent.change(portrait, { target: { files: [new File(["b"], "me.png", { type: "image/png" })] } });
    await act(async () => newer.resolve(new Blob(["me"], { type: "image/jpeg" })));
    await act(async () => earlier.resolve(new Blob(["someone else"], { type: "image/jpeg" })));
    fireEvent.click(await screen.findByRole("button", { name: "Try on look 1" }));
    await screen.findByRole("img", { name: "Experimental try-on of look 1" });
    const form = server.requests.find((entry) => entry.url === "/api/previews")?.body as FormData;
    expect(await (form.get("portrait") as Blob).text()).toBe("me");
  });

  it("keeps an input cleared while its earlier photo was still being prepared", async () => {
    const server = await prepareTryOn(installServer({ plans: [firstPlan] }));
    const slow = deferred<Blob>();
    vi.mocked(normalizePhoto).mockImplementationOnce(() => slow.promise);
    const portrait = screen.getByLabelText("Portrait photo");
    fireEvent.change(portrait, { target: { files: [new File(["a"], "unwanted.png", { type: "image/png" })] } });
    fireEvent.change(portrait, { target: { files: [] } });
    await act(async () => slow.resolve(new Blob(["unwanted"], { type: "image/jpeg" })));
    await waitFor(() =>
      expect((screen.getByRole("button", { name: "Try on look 1" }) as HTMLButtonElement).disabled).toBe(true),
    );
    expect(server.requests.some((entry) => entry.url === "/api/previews")).toBe(false);
  });

  it("says why the plan went when a try-on finds it expired and it no longer passes the checks", async () => {
    const server = await prepareTryOn(installServer({ plans: [firstPlan] }));
    server.expireToken();
    server.restoreFails = true;
    fireEvent.click(await screen.findByRole("button", { name: "Try on look 1" }));
    expect(await screen.findByText(/Your plan expired on the server and no longer passes today's checks/)).toBeTruthy();
    expect(screen.queryByText(/The three looks passed the catalog checks/)).toBeNull();
    expect(screen.queryByRole("heading", { name: "Try a look on" })).toBeNull();
  });

  it("holds a new try-on while an update runs, and says why", async () => {
    const server = await prepareTryOn(installServer({ plans: [firstPlan, reducedBudgetPlan] }));
    const model = deferred();
    server.planGate = model.promise;
    const tryOn = (await screen.findByRole("button", { name: "Try on look 1" })) as HTMLButtonElement;
    expect(tryOn.disabled).toBe(false);
    fireEvent.change(screen.getByLabelText("Budget in EUR"), { target: { value: "160" } });
    fireEvent.click(screen.getByRole("button", { name: "Update three looks" }));
    await screen.findByRole("button", { name: "Planning looks…" });
    expect(tryOn.disabled).toBe(true);
    expect(screen.getByText(/Try-ons wait until the plan request finishes\./)).toBeTruthy();

    await act(async () => model.resolve());
    expect(await screen.findAllByText("REHMAA LEGGINGS")).not.toHaveLength(0);
    expect(tryOn.disabled).toBe(false);
    expect(screen.queryByText(/Try-ons wait until the plan request finishes\./)).toBeNull();
  });

  it("does not restore the old plan when a try-on finds it expired during an update", async () => {
    const server = await prepareTryOn(installServer({ plans: [firstPlan, reducedBudgetPlan] }));
    const connection = deferred();
    server.previewEntryGate = connection.promise;
    fireEvent.click(await screen.findByRole("button", { name: "Try on look 1" }));
    await screen.findByRole("button", { name: "Cancel try-on" });
    const model = deferred();
    server.planGate = model.promise;
    fireEvent.change(screen.getByLabelText("Budget in EUR"), { target: { value: "160" } });
    fireEvent.click(screen.getByRole("button", { name: "Update three looks" }));
    await screen.findByRole("button", { name: "Planning looks…" });

    server.expireToken();
    await act(async () => connection.resolve());
    await screen.findByText("This plan is no longer current on the server.");
    expect(server.requests.filter((request) => request.url === "/api/plans/restore")).toHaveLength(0);
    expect((screen.getByRole("button", { name: "Planning looks…" }) as HTMLButtonElement).disabled).toBe(true);

    await act(async () => model.resolve());
    expect(await screen.findAllByText("REHMAA LEGGINGS")).not.toHaveLength(0);
  });

  it("keeps the updated plan when a try-on started earlier finds its plan replaced", async () => {
    const server = await prepareTryOn(installServer({ plans: [firstPlan, reducedBudgetPlan] }));
    const connection = deferred();
    server.previewEntryGate = connection.promise;
    fireEvent.click(await screen.findByRole("button", { name: "Try on look 1" }));
    await screen.findByRole("button", { name: "Cancel try-on" });
    fireEvent.change(screen.getByLabelText("Budget in EUR"), { target: { value: "160" } });
    fireEvent.click(screen.getByRole("button", { name: "Update three looks" }));
    await screen.findAllByText("REHMAA LEGGINGS");

    await act(async () => connection.resolve());
    await screen.findByText("This plan is no longer current on the server.");
    expect(server.requests.filter((request) => request.url === "/api/plans/restore")).toHaveLength(0);
    expect(screen.getAllByText("REHMAA LEGGINGS")).not.toHaveLength(0);
  });

  it("keeps Replace available after a try-on brings back an expired plan written as 249.90", async () => {
    const server = installServer({ plans: [firstPlan] });
    render(<App />);
    await generate("Relaxed office outfits", "249.90");
    fireEvent.click(screen.getByRole("button", { name: "I agree to this processing of my photos" }));
    await screen.findByText("You agreed to this processing.");
    fireEvent.change(screen.getByLabelText("Portrait photo"), {
      target: { files: [new File(["p"], "portrait.png", { type: "image/png" })] },
    });
    fireEvent.change(screen.getByLabelText("Full-body photo"), {
      target: { files: [new File(["f"], "full.webp", { type: "image/webp" })] },
    });
    server.expireToken();
    fireEvent.click(await screen.findByRole("button", { name: "Try on look 1" }));
    await screen.findByRole("img", { name: "Experimental try-on of look 1" });
    expect(screen.queryByText(/Replace is off until you select "Update three looks"/)).toBeNull();
    const replace = screen.getAllByRole("button", { name: /^Replace .* in look 1$/ })[0] as HTMLButtonElement;
    expect(replace.disabled).toBe(false);
  });

  it("hides a try-on once the plan changes", async () => {
    await prepareTryOn(installServer({ plans: [firstPlan, reducedBudgetPlan] }));
    fireEvent.click(await screen.findByRole("button", { name: "Try on look 1" }));
    await screen.findByRole("img", { name: "Experimental try-on of look 1" });
    fireEvent.change(screen.getByLabelText("Budget in EUR"), { target: { value: "160" } });
    fireEvent.click(screen.getByRole("button", { name: "Update three looks" }));
    await screen.findAllByText("REHMAA LEGGINGS");
    expect(screen.queryByRole("img", { name: /Experimental try-on/ })).toBeNull();
    expect(screen.queryByText(/is ready/)).toBeNull();
  });

  it("takes focus from a look's button to the try-on panel, which names that look", async () => {
    installServer({ plans: [firstPlan] });
    render(<App />);
    await generate();
    fireEvent.click(screen.getAllByRole("button", { name: "Try this look on" })[2]);
    const heading = screen.getByRole("heading", { name: "Try a look on" });
    expect(document.activeElement).toBe(heading);
    const description = document.getElementById(heading.getAttribute("aria-describedby") ?? "");
    expect(description?.textContent).toContain("look 3");
  });

  it("says a finished try-on is ready and moves focus to it", async () => {
    await prepareTryOn(installServer({ plans: [firstPlan] }));
    const button = await screen.findByRole("button", { name: "Try on look 1" });
    button.focus();
    fireEvent.click(button);
    const image = await screen.findByRole("img", { name: "Experimental try-on of look 1" });
    expect(screen.getByText("Your try-on of look 1 is ready.").getAttribute("role")).toBe("status");
    expect(document.activeElement?.contains(image)).toBe(true);
  });

  it("leaves focus alone when the person moved on while the try-on ran", async () => {
    const server = await prepareTryOn(installServer({ plans: [firstPlan] }));
    const provider = deferred();
    server.previewGate = provider.promise;
    fireEvent.click(await screen.findByRole("button", { name: "Try on look 1" }));
    await screen.findByRole("button", { name: "Cancel try-on" });
    const comment = screen.getByLabelText("Anything else (optional)");
    comment.focus();
    await act(async () => provider.resolve());
    await screen.findByRole("img", { name: "Experimental try-on of look 1" });
    expect(screen.getByText("Your try-on of look 1 is ready.")).toBeTruthy();
    expect(document.activeElement).toBe(comment);
  });

  it("leaves focus on another control of the panel when the person moved there while the try-on ran", async () => {
    const server = await prepareTryOn(installServer({ plans: [firstPlan] }));
    const provider = deferred();
    server.previewGate = provider.promise;
    const button = await screen.findByRole("button", { name: "Try on look 1" });
    button.focus();
    fireEvent.click(button);
    await screen.findByRole("button", { name: "Cancel try-on" });
    const panel = screen.getByRole("heading", { name: "Try a look on" }).closest("section") as HTMLElement;
    const terms = within(panel).getByRole("link", { name: "terms" });
    terms.focus();
    await act(async () => provider.resolve());
    await screen.findByRole("img", { name: "Experimental try-on of look 1" });
    expect(screen.getByText("Your try-on of look 1 is ready.")).toBeTruthy();
    expect(document.activeElement).toBe(terms);
  });

  it("does not move focus again when a person comes back to a look with a try-on", async () => {
    await prepareTryOn(installServer({ plans: [firstPlan] }));
    fireEvent.click(await screen.findByRole("button", { name: "Try on look 1" }));
    await screen.findByRole("img", { name: "Experimental try-on of look 1" });
    fireEvent.click(screen.getByRole("radio", { name: "look 2" }));
    expect(screen.queryByText(/is ready/)).toBeNull();
    const first = screen.getByRole("radio", { name: "look 1" });
    first.focus();
    fireEvent.click(first);
    await screen.findByRole("img", { name: "Experimental try-on of look 1" });
    expect(document.activeElement).toBe(first);
  });

  it("says that a try-on the provider could not finish still counts", async () => {
    const server = await prepareTryOn(installServer({ plans: [firstPlan] }));
    server.previewOutcome = "provider";
    fireEvent.click(await screen.findByRole("button", { name: "Try on look 1" }));
    expect(await screen.findByText(/still counts against this invite/)).toBeTruthy();
  });

  it("keeps the keyboard on the consent button that replaces the one pressed", async () => {
    installServer({ plans: [firstPlan] });
    render(<App />);
    await generate();
    const agree = screen.getByRole("button", { name: "I agree to this processing of my photos" });
    agree.focus();
    fireEvent.click(agree);
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("button", { name: "Withdraw consent" })));
    fireEvent.click(document.activeElement as HTMLElement);
    await screen.findByText(/Consent withdrawn/);
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "I agree to this processing of my photos" }));
  });

  it("says that a try-on that ran out of time may still count", async () => {
    const server = await prepareTryOn(installServer({ plans: [firstPlan] }));
    server.previewOutcome = "timeout";
    fireEvent.click(await screen.findByRole("button", { name: "Try on look 1" }));
    expect(await screen.findByText(/took too long\. Try again\. This try-on may still count against this invite\./)).toBeTruthy();
  });

  it("explains an unsupported photo format", async () => {
    installServer({ plans: [firstPlan] });
    render(<App />);
    await generate();
    fireEvent.click(screen.getByRole("button", { name: "I agree to this processing of my photos" }));
    await screen.findByText("You agreed to this processing.");
    fireEvent.change(screen.getByLabelText("Portrait photo"), {
      target: { files: [new File(["h"], "portrait.heic", { type: "image/heic" })] },
    });
    expect(await screen.findByText(/Most Compatible/)).toBeTruthy();
  });

  it("withdrawing consent stops a running try-on, and its late answer restores nothing", async () => {
    const server = await prepareTryOn(installServer({ plans: [firstPlan] }));
    const provider = deferred();
    server.previewGate = provider.promise;
    fireEvent.click(await screen.findByRole("button", { name: "Try on look 1" }));
    await screen.findByRole("button", { name: "Cancel try-on" });

    fireEvent.click(screen.getByRole("button", { name: "Withdraw consent" }));
    const sent = server.requests.find((entry) => entry.url === "/api/previews");
    expect(sent?.signal?.aborted).toBe(true);
    expect(screen.getByText(/your photos and result were removed from this page/)).toBeTruthy();
    expect((screen.getByLabelText("Portrait photo") as HTMLInputElement).files?.length ?? 0).toBe(0);
    expect(await screen.findByText(/Consent withdrawn/)).toBeTruthy();
    await screen.findByRole("button", { name: "I agree to this processing of my photos" });
    const consent = server.requests.filter((entry) => entry.url === "/api/session/photo-consent").at(-1);
    expect(object(consent?.body)).toEqual({ consent: false });

    const sessionReads = () => server.requests.filter((entry) => entry.url === "/api/session").length;
    const before = sessionReads();
    provider.resolve(); // the provider answers after all
    await waitFor(() => expect(server.previewsAnswered).toBe(1));
    await act(async () => new Promise((settle) => setTimeout(settle, 50)));
    expect(URL.createObjectURL).not.toHaveBeenCalled();
    expect(screen.queryByRole("img", { name: /Experimental try-on/ })).toBeNull();
    expect(sessionReads()).toBe(before); // no refresh that could race the withdrawal
    expect(screen.getByText(/Consent withdrawn/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "I agree to this processing of my photos" }));
    await screen.findByText("You agreed to this processing.");
    expect((screen.getByRole("button", { name: "Try on look 1" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("drops a photo still being prepared at withdrawal, and works again after a new consent", async () => {
    installServer({ plans: [firstPlan] });
    render(<App />);
    await generate();
    fireEvent.click(screen.getByRole("button", { name: "I agree to this processing of my photos" }));
    await screen.findByText("You agreed to this processing.");
    const slow = deferred<Blob>();
    vi.mocked(normalizePhoto).mockImplementationOnce(() => slow.promise);
    fireEvent.change(screen.getByLabelText("Portrait photo"), {
      target: { files: [new File(["p"], "portrait.png", { type: "image/png" })] },
    });
    await screen.findByRole("button", { name: "Preparing photo…" });

    fireEvent.click(screen.getByRole("button", { name: "Withdraw consent" }));
    fireEvent.click(await screen.findByRole("button", { name: "I agree to this processing of my photos" }));
    await screen.findByText("You agreed to this processing.");
    await act(async () => {
      slow.resolve(new Blob(["late"], { type: "image/jpeg" })); // finishes after the new consent
      await slow.promise;
    });
    fireEvent.change(screen.getByLabelText("Full-body photo"), {
      target: { files: [new File(["f"], "full.webp", { type: "image/webp" })] },
    });
    const tryOn = await screen.findByRole("button", { name: "Try on look 1" });
    expect((tryOn as HTMLButtonElement).disabled).toBe(true); // the old portrait did not come back

    fireEvent.change(screen.getByLabelText("Portrait photo"), {
      target: { files: [new File(["p2"], "portrait-2.png", { type: "image/png" })] },
    });
    await waitFor(() => expect((tryOn as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(tryOn);
    expect(await screen.findByRole("img", { name: "Experimental try-on of look 1" })).toBeTruthy();
  });

  it("keeps Try on disabled until both new photos are prepared, then sends a single try-on", async () => {
    const server = await prepareTryOn(installServer({ plans: [firstPlan] }));
    const tryOn = await screen.findByRole("button", { name: "Try on look 1" });
    await waitFor(() => expect((tryOn as HTMLButtonElement).disabled).toBe(false));
    const portrait = deferred<Blob>();
    const fullBody = deferred<Blob>();
    vi.mocked(normalizePhoto)
      .mockImplementationOnce(() => portrait.promise)
      .mockImplementationOnce(() => fullBody.promise);
    fireEvent.change(screen.getByLabelText("Portrait photo"), {
      target: { files: [new File(["p2"], "portrait-2.png", { type: "image/png" })] },
    });
    fireEvent.change(screen.getByLabelText("Full-body photo"), {
      target: { files: [new File(["f2"], "full-2.webp", { type: "image/webp" })] },
    });
    await act(async () => {
      portrait.resolve(new Blob(["portrait 2"], { type: "image/jpeg" }));
      await portrait.promise;
    });
    // The full-body photo is still being prepared, so no try-on can start yet.
    const waiting = screen.getByRole("button", { name: "Preparing photo…" });
    expect((waiting as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(waiting);

    const provider = deferred();
    server.previewGate = provider.promise;
    await act(async () => {
      fullBody.resolve(new Blob(["full body 2"], { type: "image/jpeg" }));
      await fullBody.promise;
    });
    fireEvent.click(screen.getByRole("button", { name: "Try on look 1" }));
    await screen.findByRole("button", { name: "Cancel try-on" });
    expect(screen.queryByRole("button", { name: "Try on look 1" })).toBeNull();
    await act(async () => provider.resolve());
    expect(await screen.findByRole("img", { name: "Experimental try-on of look 1" })).toBeTruthy();
    expect(server.requests.filter((entry) => entry.url === "/api/previews")).toHaveLength(1);
  });

  it("says so when a withdrawal could not be saved, and still clears the photos", async () => {
    const server = await prepareTryOn(installServer({ plans: [firstPlan] }));
    server.consentFails = true;
    fireEvent.click(screen.getByRole("button", { name: "Withdraw consent" }));
    expect(await screen.findByText(/The withdrawal could not be saved/)).toBeTruthy();
    expect(screen.queryByText(/Consent withdrawn/)).toBeNull();
    expect((screen.getByLabelText("Portrait photo") as HTMLInputElement).files?.length ?? 0).toBe(0);
    expect(screen.getByText("You agreed to this processing.")).toBeTruthy();
  });

  it("says in the try-on panel, not over the plan, that an agreement could not be saved", async () => {
    const server = installServer({ plans: [firstPlan] });
    render(<App />);
    await generate();
    server.consentFails = true;
    fireEvent.click(screen.getByRole("button", { name: "I agree to this processing of my photos" }));
    const message = await screen.findByText("store down");
    expect(message.closest(".tryon-panel")).not.toBeNull();
    expect(screen.getByText(/The three looks passed the catalog checks/)).toBeTruthy();
    expect(screen.queryByText(/The looks below are from the last successful request/)).toBeNull();
  });

  it("withdrawing consent removes a shown try-on and releases its image", async () => {
    await prepareTryOn(installServer({ plans: [firstPlan] }));
    fireEvent.click(await screen.findByRole("button", { name: "Try on look 1" }));
    await screen.findByRole("img", { name: "Experimental try-on of look 1" });
    fireEvent.click(screen.getByRole("button", { name: "Withdraw consent" }));
    expect(screen.queryByRole("img", { name: /Experimental try-on/ })).toBeNull();
    await waitFor(() => expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:tryon"));
    expect((screen.getByLabelText("Full-body photo") as HTMLInputElement).files?.length ?? 0).toBe(0);
  });

  it("reports a closed image budget and keeps the plan", async () => {
    const server = await prepareTryOn(installServer({ plans: [firstPlan] }));
    server.previewOutcome = "budget";
    fireEvent.click(await screen.findByRole("button", { name: "Try on look 1" }));
    expect(await screen.findByText(/shared budget for this kind of request is used up/)).toBeTruthy();
    expect(screen.getByRole("heading", { name: "look 1" })).toBeTruthy();
  });
});

describe(`${FIXTURE_LABEL}: feedback`, () => {
  it("sends the four answers and can delete them", async () => {
    const server = installServer();
    render(<App />);
    await screen.findByRole("heading", { name: "Tell us how it went" });
    const form = within(screen.getByRole("heading", { name: "Tell us how it went" }).closest("section") as HTMLElement);
    expect(form.getByText(/is deleted on 9 November 2026 at 10:00 Vienna time\./)).toBeTruthy();
    fireEvent.click(form.getByRole("radio", { name: "Yes" }));
    fireEvent.click(within(form.getByRole("group", { name: /How useful/ })).getByRole("radio", { name: "4" }));
    fireEvent.click(form.getByRole("radio", { name: "I can't judge it" }));
    fireEvent.change(form.getByLabelText(/A problem with the products/), { target: { value: "No shoes in 38" } });
    fireEvent.click(form.getByRole("button", { name: "Send feedback" }));
    await screen.findByText("Thank you. Your feedback is saved.");
    const sent = server.requests.find((entry) => entry.url === "/api/feedback");
    expect(object(sent?.body)).toEqual({
      would_wear: true,
      usefulness: 4,
      try_on: "cannot_assess",
      try_on_issues: null,
      catalog_problem: "No shoes in 38",
      comment: "",
    });
    fireEvent.click(await screen.findByRole("button", { name: "Delete answer 1" }));
    await screen.findByText("Your feedback was deleted.");
    await waitFor(() => expect(screen.queryByRole("button", { name: "Delete answer 1" })).toBeNull());
  });

  it("keeps 'no problems' apart from problems, and asks only after a try-on score", async () => {
    const server = installServer();
    render(<App />);
    await screen.findByRole("heading", { name: "Tell us how it went" });
    const form = within(screen.getByRole("heading", { name: "Tell us how it went" }).closest("section") as HTMLElement);
    const issues = form.getByRole("group", { name: /What did you notice in the try-on/ });
    const box = (name: string) => within(issues).getByRole("checkbox", { name }) as HTMLInputElement;

    expect((issues as HTMLFieldSetElement).disabled).toBe(true); // no try-on score yet
    fireEvent.click(form.getByRole("radio", { name: "I can't judge it" }));
    expect((issues as HTMLFieldSetElement).disabled).toBe(true);
    fireEvent.click(within(form.getByRole("group", { name: /How good was the try-on/ })).getByRole("radio", { name: "2" }));
    expect((issues as HTMLFieldSetElement).disabled).toBe(false);

    fireEvent.click(box("It did not look like me"));
    fireEvent.click(box("The background looked wrong"));
    fireEvent.click(box("I noticed no problems"));
    expect([box("It did not look like me").checked, box("I noticed no problems").checked]).toEqual([false, true]);
    fireEvent.click(box("Body proportions looked off"));
    expect([box("Body proportions looked off").checked, box("I noticed no problems").checked]).toEqual([true, false]);

    fireEvent.click(form.getByRole("radio", { name: "No" }));
    fireEvent.click(within(form.getByRole("group", { name: /How useful/ })).getByRole("radio", { name: "3" }));
    fireEvent.click(form.getByRole("button", { name: "Send feedback" }));
    await screen.findByText("Thank you. Your feedback is saved.");
    const sent = object(server.requests.find((entry) => entry.url === "/api/feedback")?.body);
    expect(sent).toMatchObject({ try_on: 2, try_on_issues: ["body_proportions"] });
    expect(box("Body proportions looked off").checked).toBe(false); // the form starts afresh
  });
});

/* Design My Look responses on real catalog records. They are deterministic UI fixtures, not model
 * output; the server's code decides each match's status, and these are the shapes it produces. */
function matched(status: "close" | "alternative", variantId: string, differences: string[] = []): Body {
  return { status, variant_id: variantId, offer_id: `${variantId}-offer`, differences };
}

const NO_MATCH: Body = { status: "none", variant_id: null, offer_id: null, differences: [] };

function conceptItem(slot: string, kind: string, color: string, match: Body, words: Body = {}): Body {
  return { slot, kind, color, color_family: color, material: "", fit: "", details: "", ...words, match };
}

const MIXED_ITEMS = [
  // A close match: the record confirms the kind, the color and 95% cotton; the concept names no cut
  conceptItem("top", "t-shirt", "black", matched("close", "tt-basic-t-shirt-deep-black-l"), {
    material: "cotton",
    details: "crew neck",
  }),
  conceptItem(
    "bottom",
    "trousers",
    "black",
    matched("alternative", "tt-leggings-lederoptik-deep-black-l", ["kind: leggings, not trousers"]),
  ),
  conceptItem(
    "footwear",
    "loafers",
    "white",
    matched("alternative", "tt-sneaker-mesh-einsatz-white-40", ["kind: sneakers, not loafers"]),
  ),
  conceptItem("accessory", "bag", "black", NO_MATCH),
];

const COMPLETE_ITEMS = [
  conceptItem("top", "t-shirt", "black", matched("close", "tt-basic-t-shirt-deep-black-l")),
  conceptItem("bottom", "trousers", "black", matched("close", "tt-relaxed-fit-hose-deep-black-l")),
  conceptItem("footwear", "sneakers", "white", matched("close", "tt-sneaker-mesh-einsatz-white-40")),
];

const DIFFERING_ITEMS = [
  COMPLETE_ITEMS[0],
  conceptItem(
    "bottom",
    "trousers",
    "black",
    matched("alternative", "tt-relaxed-fit-hose-deep-black-l", ["material: polyester, not linen"]),
    { material: "linen" },
  ),
  COMPLETE_ITEMS[2],
];

function conceptResponse(shoppable: Body = {}, items: Body[] = MIXED_ITEMS, fields: Body = {}): Body {
  return {
    outcome: "success",
    error: null,
    catalog_id: object(catalog).id,
    concept_token: null, // the mock server issues it
    concept: { id: "concept_1", name: "Black and white basics", why: "Plain pieces for an office week.", items },
    matching_failed: false,
    // 19.99 + 39.99 + 44.99: the three products of the mixed concept, each once
    shoppable: { complete: false, passes_hard_checks: false, matched_total_cents: 10497, violations: [], ...shoppable },
    ...fields,
  };
}

function conceptFailure(outcome: string, error: string): Body {
  return {
    outcome,
    error,
    catalog_id: object(catalog).id,
    concept_token: null,
    concept: null,
    matching_failed: false,
    shoppable: null,
  };
}

function conceptRequests(server: Server) {
  return server.requests.filter((entry) => entry.url === "/api/concepts");
}

/** Switch to Design my look, fill the two required fields and send; the caller awaits the answer. */
async function requestConcept(styleGoal = "Black and white office basics", budget = "200") {
  await screen.findByText(/products\. Prices checked/);
  fireEvent.click(screen.getByRole("radio", { name: "Design my look (experimental)" }));
  fireEvent.change(screen.getByLabelText("What should the looks work for?"), { target: { value: styleGoal } });
  fireEvent.change(screen.getByLabelText("Budget in EUR"), { target: { value: budget } });
  fireEvent.click(screen.getByRole("button", { name: "Design my look" }));
}

describe(`${FIXTURE_LABEL}: Design my look`, () => {
  it("adds nothing while the design flag is off", async () => {
    installServer();
    render(<App />);
    await screen.findByText(/products\. Prices checked/);
    expect(screen.queryByRole("group", { name: "Mode" })).toBeNull();
    expect(screen.queryByRole("radio", { name: "Design my look (experimental)" })).toBeNull();
    expect(screen.queryByText("Design my look does not use items you own.")).toBeNull();
    expect(screen.queryByText(/Design my look sends the same choices/)).toBeNull();
    expect(screen.getByText(/FullAura turns off the image model's own web and image search/)).toBeTruthy();
  });

  it("switches mode by radio, leaves out owned items, and sends only the brief and the four checked fields", async () => {
    const server = installServer({ design: true, concepts: [conceptResponse()] });
    render(<App />);
    await screen.findByText(/products\. Prices checked/);
    const modes = within(screen.getByRole("group", { name: "Mode" }));
    expect((modes.getByRole("radio", { name: "Three looks from real products" }) as HTMLInputElement).checked).toBe(true);
    fireEvent.click(screen.getByText("Items you already own", { selector: "summary span" }));
    fireEvent.click(screen.getByRole("button", { name: "Add item" })); // an owned item a plan would send

    fireEvent.click(modes.getByRole("radio", { name: "Design my look (experimental)" }));
    expect(screen.queryByText("Items you already own")).toBeNull();
    expect(screen.getByText("Design my look does not use items you own.")).toBeTruthy();
    expect(screen.getByRole("heading", { level: 1, name: "Your look first. Then real products." })).toBeTruthy();
    // One look, so the budget is not split between looks
    expect(screen.queryByRole("group", { name: "Budget applies to" })).toBeNull();
    expect(document.getElementById("budget-meaning")?.textContent).toMatch(/is the limit for the one look/);
    fireEvent.change(screen.getByLabelText("Letter size"), { target: { value: "M" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "wool" }));
    await requestConcept();
    await screen.findByText("Your concept is ready.");

    const [sent] = conceptRequests(server);
    expect(sent.headers["x-operation-id"]).toMatch(/^[0-9a-f-]{36}$/);
    expect(Object.keys(object(sent.body)).sort()).toEqual(["brief", "state"]);
    expect(object(sent.body).state).toEqual({
      budget: { amount_cents: 20000, mode: "per_look" },
      material_exclusions: ["wool"],
      footwear: "closed",
      fit: { sizes: [{ system: "letter", value: "M" }] },
    });
    expect(object(object(sent.body).brief)).toMatchObject({ style_goal: "Black and white office basics" });
    expect(planRequests(server)).toHaveLength(0);
    expect(screen.getByRole("button", { name: "Design another" })).toBeTruthy();
  });

  it("shows a close product with its facts and link, an alternative after its differences, and no match", async () => {
    const server = installServer({ design: true, concepts: [conceptResponse()] });
    render(<App />);
    await requestConcept();
    await screen.findByText("Your concept is ready.");
    expect("fit" in object(object(conceptRequests(server)[0].body).state)).toBe(false); // no fit was entered

    const card = within(screen.getByRole("article", { name: "Black and white basics" }));
    expect(card.getByText(/^A concept, not a product list\. For each item FullAura looked for the closest real product/)).toBeTruthy();
    expect(card.getByText("Plain pieces for an office week.")).toBeTruthy();
    const item = (name: string) => within(card.getByRole("heading", { name }).closest("li") as HTMLElement);

    const top = item("Black t-shirt");
    expect(top.getByText("Top")).toBeTruthy();
    expect(top.getByText("cotton, crew neck")).toBeTruthy();
    expect(top.getByText("Close match")).toBeTruthy();
    expect(top.getByText("Basic T-Shirt mit Rundhalsausschnitt")).toBeTruthy();
    expect(top.getByText("Tom Tailor, deep black, Size L")).toBeTruthy();
    expect(top.getByText(/^Materials: /)).toBeTruthy();
    expect(top.getByText(/^€\s19,99$/)).toBeTruthy();
    expect(top.getByText("Price checked 30 Oct 2026")).toBeTruthy();
    const link = top.getByRole("link", { name: "Tom Tailor" });
    expect(link.getAttribute("href")).toMatch(/^https:\/\//);
    expect([link.getAttribute("rel"), link.getAttribute("target")]).toEqual(["noreferrer", "_blank"]);

    const bottom = item("Black trousers");
    const label = bottom.getByText("Alternative, not a confirmed match:");
    const difference = bottom.getByText("kind: leggings, not trousers");
    const product = bottom.getByText("Leggings in Lederoptik");
    // The differences come first, and an alternative is never called a close match.
    expect(label.compareDocumentPosition(difference) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(difference.compareDocumentPosition(product) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(bottom.queryByText("Close match")).toBeNull();
    expect(item("White loafers").getByText("kind: sneakers, not loafers")).toBeTruthy();

    const bag = item("Black bag");
    expect(bag.getByText("No close match in our catalog")).toBeTruthy();
    expect(bag.queryByRole("link")).toBeNull();
    expect(screen.queryByRole("button", { name: /^(Replace|Keep) / })).toBeNull();
  });

  it.each([
    {
      name: "a partial match",
      response: conceptResponse(),
      texts: [/^Close matches and alternatives together: €\s104,97, each product once\. Not every item can be bought as shown\.$/],
    },
    {
      name: "a complete look that passes",
      response: conceptResponse({ complete: true, passes_hard_checks: true }, COMPLETE_ITEMS),
      texts: ["Every item has a close match, and these products pass the budget, size, shoe, material, delivery and stock checks."],
    },
    {
      // The d7 shape: every product passes the hard checks, and one still differs from the concept
      name: "products that pass the checks but differ from the concept",
      response: conceptResponse({ passes_hard_checks: true }, DIFFERING_ITEMS),
      texts: [
        /^Close matches and alternatives together: €\s104,97, each product once\. Not every item can be bought as shown\.$/,
        "These products together pass the budget, size, shoe, material, delivery and stock checks, but they differ from the concept as listed above.",
      ],
    },
    {
      name: "products that differ from the concept and fail a check",
      response: conceptResponse({ violations: ["EUR 104.97 for the look, over the EUR 100.00 budget"] }, DIFFERING_ITEMS),
      texts: ["These products together fail:", "EUR 104.97 for the look, over the EUR 100.00 budget"],
    },
    {
      name: "a complete look that fails a check",
      response: conceptResponse(
        { complete: true, violations: ["budget: EUR 104.97 for the look, over EUR 100.00"] },
        COMPLETE_ITEMS,
      ),
      texts: ["Every item has a close match, but:", "budget: EUR 104.97 for the look, over EUR 100.00"],
    },
    {
      // No €0.00 total for a search that found nothing
      name: "a search that found nothing",
      response: conceptResponse(
        { matched_total_cents: 0 },
        MIXED_ITEMS.map((entry) => ({ ...entry, match: NO_MATCH })),
      ),
      texts: ["No item has a close match or an alternative in our catalog."],
    },
    {
      name: "a product search that did not finish",
      response: conceptResponse(
        { matched_total_cents: 0 },
        MIXED_ITEMS.map((entry) => ({ ...entry, match: NO_MATCH })),
        { matching_failed: true },
      ),
      texts: ["The product search did not finish, so no products are shown."],
    },
  ])("sums up $name", async ({ response, texts }) => {
    installServer({ design: true, concepts: [response] });
    render(<App />);
    await requestConcept();
    await screen.findByText("Your concept is ready.");
    for (const text of texts) expect(screen.getByText(text)).toBeTruthy();
    const summaries = [/^Close matches and alternatives/, /^Every item has a close match/, /^The product search/, /^No item has/];
    expect(summaries.filter((summary) => screen.queryAllByText(summary).length > 0)).toHaveLength(1);
    // A hard-check line only for products that were checked together
    const { passes_hard_checks: passes, violations, complete } = object(object(response).shoppable);
    const checked = !complete && (passes === true || (violations as string[]).length > 0);
    expect(screen.queryAllByText(/^These products together/)).toHaveLength(checked ? 1 : 0);
    if (object(response).matching_failed) expect(screen.queryByText("No close match in our catalog")).toBeNull();
  });

  it("explains a failed concept, and keeps the last one when a later request times out", async () => {
    installServer({
      design: true,
      concepts: [
        conceptFailure("generation_failed", "The concept broke these rules: the look needs closed shoes"),
        conceptResponse(),
        conceptFailure("provider_timeout", "Planning took too long"),
      ],
    });
    render(<App />);
    await requestConcept();
    expect(
      await screen.findByText(
        "No concept passed the checks. Adjust your preferences and try again. The concept broke these rules: the look needs closed shoes",
      ),
    ).toBeTruthy();
    expect(screen.getByRole("heading", { name: "No concept yet" })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Design my look" }));
    await screen.findByText("Your concept is ready.");
    fireEvent.click(screen.getByRole("button", { name: "Design another" }));
    expect(await screen.findByText(/^The design request timed out\./)).toBeTruthy();
    expect(screen.getByText(/The concept below is from the last successful request\./)).toBeTruthy();
    expect(screen.getByRole("article", { name: "Black and white basics" })).toBeTruthy();
  });

  it.each([
    { name: "complete with an alternative and no match", shoppable: { complete: true, passes_hard_checks: true } },
    { name: "checked with an item that has no product", shoppable: { passes_hard_checks: true } },
  ])("shows no look whose summary disagrees with its items: $name", async ({ shoppable }) => {
    installServer({ design: true, concepts: [conceptResponse(shoppable)] });
    render(<App />);
    await requestConcept();
    expect(await screen.findByText("The request could not be completed. Check your connection and try again.")).toBeTruthy();
    expect(screen.queryByRole("article", { name: "Black and white basics" })).toBeNull();
    expect(screen.queryByText(/pass the budget, size, shoe/)).toBeNull();
  });

  async function prepareConceptTryOn(server: Server) {
    render(<App />);
    await requestConcept();
    await screen.findByText("Your concept is ready.");
    fireEvent.click(screen.getByRole("button", { name: "I agree to this processing of my photos" }));
    await screen.findByText("You agreed to this processing.");
    fireEvent.change(screen.getByLabelText("Portrait photo"), {
      target: { files: [new File(["p"], "portrait.png", { type: "image/png" })] },
    });
    fireEvent.change(screen.getByLabelText("Full-body photo"), {
      target: { files: [new File(["f"], "full.webp", { type: "image/webp" })] },
    });
    return server;
  }

  it("tries the concept on with its token alone, and a new concept hides the result", async () => {
    const server = await prepareConceptTryOn(installServer({ design: true, concepts: [conceptResponse(), conceptResponse()] }));
    expect(screen.queryByRole("group", { name: "Look to try on" })).toBeNull(); // one look only
    fireEvent.click(await screen.findByRole("button", { name: "Try on the concept" }));
    expect(await screen.findByRole("img", { name: "Experimental try-on of the concept" })).toBeTruthy();
    expect(screen.getByText(/Experimental visualization/)).toBeTruthy();
    const headers = server.requests.find((entry) => entry.url === "/api/previews")?.headers ?? {};
    expect(headers["x-concept-token"]).toBe("concept-token-1");
    expect(Object.keys(headers).filter((name) => name === "x-plan-token" || name === "x-look-id")).toEqual([]);

    fireEvent.click(screen.getByRole("button", { name: "Design another" }));
    await waitFor(() => expect(server.conceptToken).toBe("concept-token-2"));
    await waitFor(() => expect(screen.queryByRole("img", { name: /Experimental try-on/ })).toBeNull());
  });

  it("says when the concept was replaced, and tries no plan restore", async () => {
    const server = await prepareConceptTryOn(installServer({ design: true, concepts: [conceptResponse()] }));
    server.conceptToken = "concept-token-from-another-tab";
    fireEvent.click(await screen.findByRole("button", { name: "Try on the concept" }));
    expect(await screen.findByText("This concept was replaced. Try on the current one.")).toBeTruthy();
    expect(server.requests.filter((entry) => entry.url.startsWith("/api/plans"))).toEqual([]);
  });
});
