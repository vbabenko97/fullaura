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
  previewOutcome: "image" | "budget";
  restoreFails: boolean;
  catalogFailures: number;
  consentFails: boolean;
  /** When set, a try-on answers only once this settles, as a slow provider would. */
  previewGate: Promise<void> | null;
  previewsAnswered: number;
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
  options: { invited?: boolean; aged?: boolean; plans?: unknown[]; accounting?: boolean; catalog?: unknown } = {},
): Server {
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
      records_purge_at: "2026-11-09T10:00:00+01:00",
      feedback_ids: [],
      has_plan: false,
    },
    expireToken: () => {
      server.token += 100;
    },
    previewOutcome: "image",
    restoreFails: false,
    catalogFailures: 0,
    consentFails: false,
    previewGate: null,
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
      if (server.restoreFails) {
        return json({
          outcome: "restore_failed",
          plan: null,
          violations: [{ rule: "budget", detail: "over budget" }],
          conflicts: [],
          notices: [],
          error: null,
          catalog_id: object(catalog).id,
          catalog_changed: true,
        });
      }
      server.token += 1;
      return json({ ...success(object(object(body).state).plan), catalog_changed: false });
    }
    if (url === "/api/previews") {
      if (headers["x-plan-token"] !== currentToken()) return json({ detail: "expired", reason: "plan_expired" }, 409);
      if (server.previewOutcome === "budget") return json({ detail: "budget", reason: "budget" }, 503);
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
  await screen.findByText(/products\. Price checked/);
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
    expect(notice.getByText(/If the look includes an item you own, the description includes the color/)).toBeTruthy();
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

  it("says next to the button what the budget limits, and totals the plan as the shopping list does", async () => {
    installServer({ plans: [firstPlan] });
    render(<App />);
    await screen.findByText(/products\. Price checked/);
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

  it("says so when a withdrawal could not be saved, and still clears the photos", async () => {
    const server = await prepareTryOn(installServer({ plans: [firstPlan] }));
    server.consentFails = true;
    fireEvent.click(screen.getByRole("button", { name: "Withdraw consent" }));
    expect(await screen.findByText(/The withdrawal could not be saved/)).toBeTruthy();
    expect(screen.queryByText(/Consent withdrawn/)).toBeNull();
    expect((screen.getByLabelText("Portrait photo") as HTMLInputElement).files?.length ?? 0).toBe(0);
    expect(screen.getByText("You agreed to this processing.")).toBeTruthy();
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
