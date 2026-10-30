import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { crc32, deflateSync } from "node:zlib";

/* The whole beta flow against scripted providers: invite, plan, try-on, keep, lower budget,
 * update, replace, save, restore, export and feedback. Nothing here is model output. */
const INVITE = "e2e-invite-code-not-for-production";
const SHOTS = "../runs/e2e/screens";

function png(width: number, height: number, rgb: [number, number, number]): Buffer {
  const row = Buffer.concat([Buffer.from([0]), Buffer.from(Array.from({ length: width }, () => rgb).flat())]);
  const chunk = (kind: string, data: Buffer) => {
    const body = Buffer.concat([Buffer.from(kind, "ascii"), data]);
    const length = Buffer.alloc(4);
    length.writeUInt32BE(data.length);
    const sum = Buffer.alloc(4);
    sum.writeUInt32BE(crc32(body));
    return Buffer.concat([length, body, sum]);
  };
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0);
  header.writeUInt32BE(height, 4);
  header.set([8, 2, 0, 0, 0], 8);
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", header),
    chunk("IDAT", deflateSync(Buffer.concat(Array.from({ length: height }, () => row)))),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

async function shot(page: Page, project: string, step: string) {
  await page.screenshot({ path: `${SHOTS}/${project}-${step}.png`, fullPage: true });
}

test("an invited person plans, tries on, edits, saves, restores, exports and gives feedback", async ({
  page,
}, testInfo) => {
  const project = testInfo.project.name;
  await page.addInitScript(() => {
    window.print = () => {
      document.body.dataset.printed = "yes";
    };
  });

  await page.goto(`/#invite=${INVITE}`);
  const age = page.getByRole("checkbox", { name: "I confirm that I am 18 or older." });
  const preferences = page.getByRole("heading", { name: "Your preferences" });
  await expect(age.or(preferences)).toBeVisible();
  expect(page.url()).not.toContain("invite=");
  if (await age.isVisible()) {
    await shot(page, project, "01-age");
    await age.check();
    await page.getByRole("button", { name: "Continue" }).click();
  }
  await expect(preferences).toBeVisible();

  await page.getByLabel("Occasion").selectOption("office");
  await page.getByLabel("Season").selectOption("autumn");
  await page.getByLabel("What should the looks work for?").fill("Smart casual for autumn office days");
  await page.getByLabel("Colors you prefer").fill("navy, grey, black");
  await page.getByLabel("Budget in EUR").fill("450");
  await page.getByRole("checkbox", { name: "Men's", exact: true }).check();
  await page.getByLabel("Letter size").selectOption("M");
  await page.getByLabel("Waist sizes").fill("32/32");
  await page.getByLabel("EU shoe sizes").fill("42, 43");
  await expect(page.getByRole("radio", { name: "Closed shoes" })).toBeChecked();
  await shot(page, project, "02-preferences");
  await page.getByRole("button", { name: "Generate three looks" }).click();
  await expect(page.getByText(/The three looks passed the catalog checks/)).toBeVisible();
  await expect(page.getByRole("heading", { name: "look 3" })).toBeVisible();
  await shot(page, project, "03-plan");

  const consent = page.getByRole("button", { name: "I agree to this processing of my photos" });
  if (await consent.isVisible()) await consent.click();
  await expect(page.getByText("You agreed to this processing.")).toBeVisible();
  await page.getByLabel("Portrait photo").setInputFiles({
    name: "portrait.png",
    mimeType: "image/png",
    buffer: png(60, 80, [200, 170, 150]),
  });
  await page.getByLabel("Full-body photo").setInputFiles({
    name: "full-body.png",
    mimeType: "image/png",
    buffer: png(60, 120, [120, 140, 160]),
  });
  await page.getByRole("button", { name: "Try on look 1" }).click();
  await expect(page.getByRole("img", { name: "Experimental try-on of look 1" })).toBeVisible();
  await expect(page.getByText(/Experimental visualization/)).toBeVisible();
  await shot(page, project, "04-try-on");

  const keep = page.getByRole("button", { name: /^Keep .* in look_1$/ }).first();
  const kept = (await keep.getAttribute("aria-label"))?.replace(/^Keep /, "Release ") ?? "";
  await keep.click();
  await page.getByLabel("Budget in EUR").fill("420");
  await page.getByRole("button", { name: "Update three looks" }).click();
  await expect(page.getByText(/The three looks passed the catalog checks/)).toBeVisible();
  await expect(page.getByRole("button", { name: kept })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("img", { name: /Experimental try-on/ })).toHaveCount(0);

  await page.getByRole("button", { name: /^Replace .* in look_2$/ }).first().click();
  await expect(page.getByText(/The item was replaced/)).toBeVisible();
  await shot(page, project, "05-replaced");

  await page.getByRole("button", { name: "Save this plan" }).click();
  await expect(page.getByText(/on this device\./).first()).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: "look 1" })).toBeVisible();
  await page.getByRole("button", { name: "Restore" }).first().click();
  await expect(page.getByText("The saved plan still passes every check.")).toBeVisible();

  const table = page.getByRole("table");
  await expect(table).toBeVisible();
  await page.getByRole("button", { name: "Copy list" }).click();
  await expect(page.getByText("Copied the list.")).toBeVisible();
  const copied = await page.evaluate(() => navigator.clipboard.readText());
  expect(copied).toContain("FullAura shopping list");
  expect(copied).toContain("Delivery and returns are not included");
  await page.getByRole("button", { name: "Print" }).click();
  await expect(page.locator("body")).toHaveAttribute("data-printed", "yes");
  await page.emulateMedia({ media: "print" });
  await expect(page.getByRole("heading", { name: "Your preferences" })).toBeHidden();
  await shot(page, project, "06-print");
  await page.emulateMedia({ media: "screen" });

  const feedback = page.locator("section", { has: page.getByRole("heading", { name: "Tell us how it went" }) });
  await feedback.getByRole("radio", { name: "Yes", exact: true }).check();
  await feedback.getByRole("group", { name: /How useful/ }).getByRole("radio", { name: "4", exact: true }).check();
  await feedback.getByRole("group", { name: /How good was the try-on/ }).getByRole("radio", { name: "3", exact: true }).check();
  await feedback.getByLabel(/A problem with the products/).fill("End-to-end check, not a real answer");
  await feedback.getByRole("button", { name: "Send feedback" }).click();
  await expect(page.getByText("Thank you. Your feedback is saved.")).toBeVisible();
  await feedback.getByRole("button", { name: /^Delete answer/ }).first().click();
  await expect(page.getByText("Your feedback was deleted.")).toBeVisible();
  await shot(page, project, "07-done");

  const horizontalOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
  );
  expect(horizontalOverflow).toBe(false);
});
