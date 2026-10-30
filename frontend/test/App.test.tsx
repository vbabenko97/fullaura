import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../src/App";
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
  requests: { url: string; body: Body | FormData | null; headers: Record<string, string> }[];
  session: Body;
  token: number;
  expireToken: () => void;
  previewOutcome: "image" | "budget";
}

function installServer(options: { invited?: boolean; aged?: boolean; plans?: unknown[]; accounting?: boolean } = {}): Server {
  const server: Server = {
    plans: options.plans ?? [],
    requests: [],
    token: 0,
    session: {
      beta: true,
      invited: options.invited ?? true,
      accounting: options.accounting ?? true,
      age_confirmed: options.aged ?? true,
      photo_consent: false,
      plan_requests_used: 0,
      plan_requests_limit: 8,
      preview_requests_used: 0,
      preview_requests_limit: 12,
      planning_open: options.accounting ?? true,
      previews_open: true,
      pilot_ends_at: null,
      pilot_ended: false,
      feedback_ids: [],
      has_plan: false,
    },
    expireToken: () => {
      server.token += 100;
    },
    previewOutcome: "image",
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
    server.requests.push({ url, body, headers });
    const session = server.session;
    if (url === "/api/session") return json(session);
    if (url === "/api/session/invite") {
      if (object(body).code !== "good-invite") return json({ detail: "invalid", reason: "invite_invalid" }, 403);
      session.invited = true;
      return json(session);
    }
    if (url === "/api/session/confirm-age") {
      session.age_confirmed = true;
      return json(session);
    }
    if (url === "/api/session/photo-consent") {
      session.photo_consent = object(body).consent;
      return json(session);
    }
    if (url === "/api/catalog") return json(catalog);
    if (url === "/api/plans") {
      const request = object(body);
      if (!headers["x-operation-id"]) return json({ detail: "no id", reason: "operation_id" }, 400);
      if (request.plan_token !== null && request.plan_token !== currentToken()) {
        return json({ detail: "expired", reason: "plan_expired" }, 409);
      }
      if (!session.planning_open) return json({ detail: "paused", reason: "accounting_unavailable" }, 503);
      session.plan_requests_used = Number(session.plan_requests_used) + 1;
      const next = server.plans.shift();
      if (next === undefined) throw new Error(`${FIXTURE_LABEL} has no queued plan`);
      if (object(next).outcome) return json(next);
      server.token += 1;
      return json(success(next));
    }
    if (url === "/api/plans/restore") {
      // Like the server, refuse any field beyond the saved brief, state and catalog ID.
      if (Object.keys(object(body)).sort().join() !== "brief,catalog_id,state") {
        return json({ detail: "extra fields", reason: "invalid" }, 422);
      }
      server.token += 1;
      return json({ ...success(object(object(body).state).plan), catalog_changed: false });
    }
    if (url === "/api/previews") {
      if (headers["x-plan-token"] !== currentToken()) return json({ detail: "expired", reason: "plan_expired" }, 409);
      if (server.previewOutcome === "budget") return json({ detail: "budget", reason: "budget" }, 503);
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
  await screen.findByText(/products, checked/);
  fireEvent.change(screen.getByLabelText("What should the looks work for?"), { target: { value: styleGoal } });
  fireEvent.change(screen.getByLabelText("Budget in EUR"), { target: { value: budget } });
  fireEvent.click(screen.getByRole("button", { name: "Generate three looks" }));
  await screen.findByText(/The three looks passed the catalog checks/);
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
  });

  it("opens the invite in the link fragment and removes it from the address", async () => {
    window.location.hash = "#invite=good-invite";
    const server = installServer({ invited: false });
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Your preferences" })).toBeTruthy();
    expect(object(server.requests[0].body)).toEqual({ code: "good-invite" });
    expect(window.location.hash).toBe("");
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
    fireEvent.click(screen.getByRole("button", { name: "Keep MAARKOS HEAVY WEIGHT T-SHIRT in look_1" }));
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
    fireEvent.click(screen.getByRole("button", { name: "Replace MAARKOS HEAVY WEIGHT T-SHIRT in look_1" }));
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

  it("pauses planning when spending cannot be recorded", async () => {
    installServer({ accounting: false });
    render(<App />);
    expect(await screen.findByText(/planning is paused/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Generate three looks" }) as HTMLButtonElement).disabled).toBe(true);
  });
});

describe(`${FIXTURE_LABEL}: shopping list and saved plans`, () => {
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
    expect(await screen.findByText("The saved plan still passes every check.")).toBeTruthy();
    const restore = server.requests.find((entry) => entry.url === "/api/plans/restore");
    expect(object(object(restore?.body).state).plan).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
    await screen.findByText("Deleted from this device.");
    expect(JSON.parse(localStorage.getItem("fullaura.saved-plans.v1") ?? "[]")).toEqual([]);
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

  it("hides a try-on once the plan changes", async () => {
    await prepareTryOn(installServer({ plans: [firstPlan, reducedBudgetPlan] }));
    fireEvent.click(await screen.findByRole("button", { name: "Try on look 1" }));
    await screen.findByRole("img", { name: "Experimental try-on of look 1" });
    fireEvent.change(screen.getByLabelText("Budget in EUR"), { target: { value: "160" } });
    fireEvent.click(screen.getByRole("button", { name: "Update three looks" }));
    await screen.findAllByText("REHMAA LEGGINGS");
    expect(screen.queryByRole("img", { name: /Experimental try-on/ })).toBeNull();
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
      catalog_problem: "No shoes in 38",
      comment: "",
    });
    fireEvent.click(await screen.findByRole("button", { name: "Delete answer 1" }));
    await screen.findByText("Your feedback was deleted.");
    await waitFor(() => expect(screen.queryByRole("button", { name: "Delete answer 1" })).toBeNull());
  });
});
