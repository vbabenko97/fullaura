import { expect, test } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";

/* Live runs against real providers with synthetic adult personas; never in CI. Each run costs
 * one planning request and one try-on per persona, counted in the beta's ledger.
 *
 *   FULLAURA_LIVE_URL=https://... FULLAURA_LIVE_INVITE=<code> \
 *   FULLAURA_LIVE_PERSONAS=../data/private/beta/personas FULLAURA_LIVE_OUT=../data/private/beta/live/<run> \
 *   npx playwright test --config playwright.live.config.ts
 */
const personasFolder = resolve(process.env.FULLAURA_LIVE_PERSONAS ?? "../data/private/beta/personas");
const out = resolve(process.env.FULLAURA_LIVE_OUT ?? "../data/private/beta/live/run");
const invite = process.env.FULLAURA_LIVE_INVITE ?? "";

interface Persona {
  name: string;
  line: "Men's" | "Women's";
  letter: string;
  waist: string;
  shoes: string;
  goal: string;
  occasion: string;
  budget: string;
  wholePlan: boolean;
  avoidWool: boolean;
}

const PERSONAS: Persona[] = [
  {
    name: "p1-men-m",
    line: "Men's",
    letter: "M",
    waist: "32/32",
    shoes: "42",
    goal: "Smart casual looks for autumn office days",
    occasion: "office",
    budget: "250",
    wholePlan: false,
    avoidWool: true,
  },
  {
    name: "p2-women-s",
    line: "Women's",
    letter: "S",
    waist: "",
    shoes: "38",
    goal: "Relaxed everyday outfits for city walks",
    occasion: "casual",
    budget: "150",
    wholePlan: false,
    avoidWool: false,
  },
  {
    name: "p3-women-l",
    line: "Women's",
    letter: "L",
    waist: "",
    shoes: "40",
    goal: "A small casual capsule that mixes and matches",
    occasion: "casual",
    budget: "300",
    wholePlan: true,
    avoidWool: true,
  },
];

for (const persona of PERSONAS) {
  test(`live: ${persona.name} plans and tries a look on`, async ({ page }) => {
    test.skip(!invite, "Set FULLAURA_LIVE_INVITE and FULLAURA_LIVE_URL");
    mkdirSync(out, { recursive: true });
    await page.goto(`/#invite=${encodeURIComponent(invite)}`);
    const age = page.getByRole("checkbox", { name: "I confirm that I am 18 or older." });
    const preferences = page.getByRole("heading", { name: "Your preferences" });
    await expect(age.or(preferences)).toBeVisible();
    if (await age.isVisible()) {
      await age.check();
      await page.getByRole("button", { name: "Continue" }).click();
    }
    await page.getByLabel("Occasion").selectOption(persona.occasion);
    await page.getByLabel("Season").selectOption("autumn");
    await page.getByLabel("What should the looks work for?").fill(persona.goal);
    await page.getByLabel("Budget in EUR").fill(persona.budget);
    if (persona.wholePlan) await page.getByRole("radio", { name: "All three looks together" }).check();
    await page.getByRole("checkbox", { name: persona.line, exact: true }).check();
    await page.getByLabel("Letter size").selectOption(persona.letter);
    await page.getByLabel("Waist sizes").fill(persona.waist);
    await page.getByLabel("EU shoe sizes").fill(persona.shoes);
    if (persona.avoidWool) {
      await page.getByText("Avoid materials").click();
      await page.getByRole("checkbox", { name: "wool", exact: true }).check();
    }
    await page.getByRole("button", { name: /three looks$/ }).click();
    const passed = page.getByText(/The three looks passed the catalog checks/);
    const failed = page.getByRole("alert");
    await expect(passed.or(failed).first()).toBeVisible({ timeout: 180_000 });
    await page.screenshot({ path: join(out, `${persona.name}-plan.png`), fullPage: true });
    if (!(await passed.isVisible())) {
      writeFileSync(join(out, `${persona.name}-plan-failed.txt`), (await failed.first().innerText()) + "\n");
      throw new Error(`No plan for ${persona.name}: ${await failed.first().innerText()}`);
    }
    const look = page.locator("article", { has: page.getByRole("heading", { name: "look 1" }) });
    writeFileSync(join(out, `${persona.name}-look1.txt`), (await look.innerText()) + "\n");

    const consent = page.getByRole("button", { name: "I agree to this processing of my photos" });
    if (await consent.isVisible()) await consent.click();
    await expect(page.getByText("You agreed to this processing.")).toBeVisible();
    await page.getByLabel("Portrait photo").setInputFiles(join(personasFolder, `${persona.name}-portrait.webp`));
    await page.getByLabel("Full-body photo").setInputFiles(join(personasFolder, `${persona.name}-full-body.webp`));
    const answer = page.waitForResponse((response) => response.url().endsWith("/api/previews"), { timeout: 150_000 });
    await page.getByRole("button", { name: "Try on look 1" }).click();
    const image = page.getByRole("img", { name: "Experimental try-on of look 1" });
    const error = page.locator(".preview-status.is-error");
    await expect(image.or(error).first()).toBeVisible({ timeout: 150_000 });
    if (await error.isVisible()) throw new Error(`Try-on failed for ${persona.name}: ${await error.innerText()}`);
    const response = await answer;
    const extension = (response.headers()["content-type"] ?? "image/png").replace("image/", "");
    writeFileSync(join(out, `${persona.name}-look1-tryon.${extension}`), await response.body());
  });
}

test("live: the whole flow on one persona", async ({ page }) => {
  test.skip(!invite || process.env.FULLAURA_LIVE_FULL !== "1", "Set FULLAURA_LIVE_FULL=1 for the whole flow");
  const persona = PERSONAS[0];
  mkdirSync(out, { recursive: true });
  await page.addInitScript(() => {
    window.print = () => {
      document.body.dataset.printed = "yes";
    };
  });
  await page.goto(`/#invite=${encodeURIComponent(invite)}`);
  const age = page.getByRole("checkbox", { name: "I confirm that I am 18 or older." });
  const preferences = page.getByRole("heading", { name: "Your preferences" });
  await expect(age.or(preferences)).toBeVisible();
  if (await age.isVisible()) {
    await age.check();
    await page.getByRole("button", { name: "Continue" }).click();
  }
  await page.getByLabel("Occasion").selectOption("office");
  await page.getByLabel("Season").selectOption("autumn");
  await page.getByLabel("What should the looks work for?").fill(persona.goal);
  await page.getByLabel("Budget in EUR").fill("300");
  await page.getByRole("checkbox", { name: persona.line, exact: true }).check();
  await page.getByLabel("Letter size").selectOption(persona.letter);
  await page.getByLabel("Waist sizes").fill(persona.waist);
  await page.getByLabel("EU shoe sizes").fill(persona.shoes);
  const passed = page.getByText(/The three looks passed the catalog checks/);
  await page.getByRole("button", { name: "Generate three looks" }).click();
  await expect(passed).toBeVisible({ timeout: 180_000 });
  await page.screenshot({ path: join(out, "full-1-plan.png"), fullPage: true });

  const consent = page.getByRole("button", { name: "I agree to this processing of my photos" });
  if (await consent.isVisible()) await consent.click();
  await page.getByLabel("Portrait photo").setInputFiles(join(personasFolder, `${persona.name}-portrait.webp`));
  await page.getByLabel("Full-body photo").setInputFiles(join(personasFolder, `${persona.name}-full-body.webp`));
  await page.getByRole("button", { name: "Try on look 1" }).click();
  await expect(page.getByRole("img", { name: "Experimental try-on of look 1" })).toBeVisible({ timeout: 150_000 });
  await page.screenshot({ path: join(out, "full-2-try-on.png"), fullPage: true });

  const keep = page.getByRole("button", { name: /^Keep .* in look 1$/ }).first();
  const kept = (await keep.getAttribute("aria-label"))?.replace(/^Keep /, "Release ") ?? "";
  await keep.click();
  await page.getByLabel("Budget in EUR").fill("260");
  await page.getByRole("button", { name: "Update three looks" }).click();
  await expect(passed.or(page.getByRole("alert")).first()).toBeVisible({ timeout: 180_000 });
  await expect(passed).toBeVisible();
  await expect(page.getByRole("button", { name: kept })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("img", { name: /Experimental try-on/ })).toHaveCount(0);

  await page.getByRole("button", { name: /^Replace .* in look 2$/ }).first().click();
  const replaced = page.getByText(/The item was replaced/);
  await expect(replaced.or(page.getByRole("alert")).first()).toBeVisible({ timeout: 180_000 });
  writeFileSync(join(out, "full-3-replace.txt"), (await page.locator(".request-message").innerText()) + "\n");
  await page.screenshot({ path: join(out, "full-3-replaced.png"), fullPage: true });

  await page.getByRole("button", { name: "Save this plan" }).click();
  await page.reload();
  await expect(page.getByRole("heading", { name: "look 1" })).toBeVisible({ timeout: 60_000 });
  await page.getByRole("button", { name: "Restore" }).first().click();
  await expect(page.getByText(/The saved plan still passes every check/)).toBeVisible({ timeout: 60_000 });
  await page.getByRole("button", { name: "Copy list" }).click();
  await page.getByRole("button", { name: "Print" }).click();
  await expect(page.locator("body")).toHaveAttribute("data-printed", "yes");

  const feedback = page.locator("section", { has: page.getByRole("heading", { name: "Tell us how it went" }) });
  await feedback.getByRole("radio", { name: "Yes", exact: true }).check();
  await feedback.getByRole("group", { name: /How useful/ }).getByRole("radio", { name: "4", exact: true }).check();
  await feedback.getByRole("radio", { name: "I can't judge it" }).check();
  await feedback.getByLabel(/A problem with the products/).fill("Operator check with a synthetic persona");
  await feedback.getByRole("button", { name: "Send feedback" }).click();
  await expect(page.getByText("Thank you. Your feedback is saved.")).toBeVisible();
  await feedback.getByRole("button", { name: /^Delete answer/ }).first().click();
  await expect(page.getByText("Your feedback was deleted.")).toBeVisible();
  await page.screenshot({ path: join(out, "full-4-done.png"), fullPage: true });
});
