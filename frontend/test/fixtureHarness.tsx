import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "../src/App";
import "../src/styles.css";
import "./fixture.css";
import firstPlanJson from "./fixtures/plan-success-initial.fixture.json?raw";
import reducedBudgetPlanJson from "./fixtures/plan-success-reduced-budget.fixture.json?raw";

const firstPlan: unknown = JSON.parse(firstPlanJson);
const reducedBudgetPlan: unknown = JSON.parse(reducedBudgetPlanJson);
const fixtureBanner =
  "Deterministic test fixture. Scripted plan; not live model output or server-validated.";
const liveFetch = window.fetch.bind(window);

window.fetch = async (input, init) => {
  const url =
    typeof input === "string"
      ? new URL(input, window.location.href)
      : input instanceof URL
        ? input
        : new URL(input.url);
  if (url.pathname !== "/api/plans") return liveFetch(input, init);

  const requestBody: unknown = JSON.parse(String(init?.body));
  const request = asRecord(requestBody);
  const state = asRecord(request.state);
  const budget = asRecord(state.budget);
  const plan = Number(budget.amount_cents) <= 16000 ? reducedBudgetPlan : firstPlan;
  return new Response(JSON.stringify({ outcome: "success", plan, conflicts: [] }), {
    headers: { "Content-Type": "application/json" },
  });
};

function asRecord(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error("The fixture request has an invalid object");
  }
  return value as Record<string, unknown>;
}

function waitFor(find: () => boolean): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => {
      observer.disconnect();
      reject(new Error("The fixture flow did not reach its expected state"));
    }, 10000);
    const observer = new MutationObserver(() => {
      if (!find()) return;
      window.clearTimeout(timer);
      observer.disconnect();
      resolve();
    });
    observer.observe(document.body, {
      attributes: true,
      characterData: true,
      childList: true,
      subtree: true,
    });
    if (find()) {
      window.clearTimeout(timer);
      observer.disconnect();
      resolve();
    }
  });
}

function setValue(selector: string, value: string): void {
  const input = document.querySelector<HTMLInputElement | HTMLTextAreaElement>(selector);
  if (!input) throw new Error(`Missing fixture control: ${selector}`);
  const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), "value")?.set;
  setter?.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
  input.dispatchEvent(new Event("change", { bubbles: true }));
}

async function runFixtureFlow(): Promise<void> {
  await waitFor(() => document.querySelector(".catalog-status")?.textContent?.includes("15 catalog products") ?? false);
  setValue("#style-goal", "Relaxed office outfits");
  setValue("#budget-amount", "250");
  document.querySelector<HTMLButtonElement>('button[type="submit"]')?.click();
  await waitFor(() => document.querySelectorAll(".look-card").length === 3);

  document
    .querySelector<HTMLButtonElement>(
      'button[aria-label="Keep MAARKOS HEAVY WEIGHT T-SHIRT in look_1"]',
    )
    ?.click();
  setValue("#budget-amount", "160");
  document.querySelector<HTMLButtonElement>('button[type="submit"]')?.click();
  await waitFor(
    () =>
      document.querySelector(".plan-total strong")?.textContent?.includes("234,40") === true &&
      document
        .querySelector<HTMLButtonElement>(
          'button[aria-label="Release MAARKOS HEAVY WEIGHT T-SHIRT in look_1"]',
        )
        ?.getAttribute("aria-pressed") === "true",
  );
  document.body.dataset.fixtureState = "ready";
}

const root = document.getElementById("root");
if (!root) throw new Error("Missing fixture page root");

createRoot(root).render(
  <StrictMode>
    <p className="fixture-banner">{fixtureBanner}</p>
    <App />
  </StrictMode>,
);

void runFixtureFlow().catch((error: unknown) => {
  document.body.dataset.fixtureState = "error";
  console.error(error);
});
