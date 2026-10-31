import { expect, test } from "@playwright/test";
import type { Locator, Page } from "@playwright/test";
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

async function openWorkspace(page: Page) {
  await page.goto(`/#invite=${INVITE}`);
  const age = page.getByRole("checkbox", { name: "I confirm that I am 18 or older." });
  const preferences = page.getByRole("heading", { name: "Your preferences" });
  await expect(age.or(preferences)).toBeVisible();
  if (await age.isVisible()) {
    await age.check();
    await page.getByRole("button", { name: "Continue" }).click();
  }
  await expect(preferences).toBeVisible();
}

/** Press Tab until the target has focus, so it is reached in the page's own order. */
async function tabTo(page: Page, target: Locator, limit = 80) {
  for (let press = 0; press < limit; press += 1) {
    if (await target.evaluate((element) => element === document.activeElement)) return;
    await page.keyboard.press("Tab");
  }
  throw new Error(`Tab did not reach ${String(target)}`);
}

async function focusRing(target: Locator): Promise<string> {
  return target.evaluate((element) => {
    const style = getComputedStyle(element);
    return `${style.outlineStyle} ${style.outlineWidth}`;
  });
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

  // Withdrawing consent removes the result and the chosen photos from the page.
  await page.getByRole("button", { name: "Withdraw consent" }).click();
  await expect(page.getByRole("img", { name: /Experimental try-on/ })).toHaveCount(0);
  await expect(page.getByText(/Consent withdrawn/)).toBeVisible();
  await expect(consent).toBeVisible();
  for (const label of ["Portrait photo", "Full-body photo"]) {
    expect(await page.getByLabel(label).evaluate((input: HTMLInputElement) => input.files?.length ?? 0)).toBe(0);
  }
  await expect(page.getByRole("button", { name: "Try on look 1" })).toBeDisabled();

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

/* The real catalog has no photo FullAura may show, so this test gives every variant an invented
 * one from the reserved synthetic.example domain; the shoes' photos fail to load on purpose. */
test("the budget and feedback controls work from the keyboard, and product photos keep their place", async ({
  page,
}, testInfo) => {
  const project = testInfo.project.name;
  const failing = new Set<string>();
  await page.route("**/api/catalog", async (route) => {
    const response = await route.fetch();
    const catalog = await response.json();
    for (const product of catalog.products) {
      for (const variant of product.variants) {
        const url = `https://images.synthetic.example/${variant.id}.png`;
        if (product.category === "footwear") failing.add(url);
        variant.image = {
          url,
          alt: `Synthetic test photo: ${product.title}`,
          width: 300,
          height: 400,
          attribution: "Synthetic test image",
        };
      }
    }
    await route.fulfill({ response, json: catalog });
  });
  await page.route("https://images.synthetic.example/**", (route) =>
    failing.has(route.request().url())
      ? route.fulfill({ status: 404, body: "" })
      : route.fulfill({ status: 200, contentType: "image/png", body: png(300, 400, [214, 206, 190]) }),
  );
  await openWorkspace(page);

  // Preferences, from the top of the page, by keyboard alone
  const goal = page.getByLabel("What should the looks work for?");
  await tabTo(page, goal);
  await page.keyboard.type("Keyboard check, not a real request");
  const budget = page.getByLabel("Budget in EUR");
  await tabTo(page, budget);
  await page.keyboard.type("150");
  const meaning = page.locator("#budget-meaning");
  await expect(meaning).toContainText("is the limit for each look on its own");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("radio", { name: "Each look" })).toBeFocused();
  await page.keyboard.press("ArrowDown");
  await expect(page.getByRole("radio", { name: "All three looks together" })).toBeChecked();
  await expect(meaning).toContainText("is the limit for all three looks together");
  await page.keyboard.press("ArrowUp");
  await expect(page.getByRole("radio", { name: "Each look" })).toBeChecked();
  const generate = page.getByRole("button", { name: "Generate three looks" });
  await tabTo(page, generate);
  expect(await focusRing(generate)).toBe("solid 2px");
  await page.keyboard.press("Enter");
  await expect(page.getByText(/The three looks passed the catalog checks/)).toBeVisible();
  await expect(page.getByText(/Each look within/)).toBeVisible();

  // Each photo has its box before it loads; a failed one keeps the box and its description.
  const photos = page.getByRole("img", { name: /^Synthetic test photo: / });
  await expect(photos.first()).toBeVisible();
  const box = await photos.first().boundingBox();
  expect(box && Math.abs(box.height / box.width - 4 / 3)).toBeLessThan(0.05);
  await page.locator(".look-card .look-item").last().scrollIntoViewIfNeeded(); // lazy photos load in view
  await expect(page.getByRole("img", { name: /: image unavailable$/ }).first()).toBeVisible();
  const missing = await page.getByRole("img", { name: /: image unavailable$/ }).first().boundingBox();
  expect(missing && Math.abs(missing.height / missing.width - 4 / 3)).toBeLessThan(0.05);
  await expect(page.getByRole("link", { name: "Tom Tailor" }).first()).toBeVisible();
  await shot(page, project, "08-synthetic-photos");

  // Feedback, by keyboard alone
  const feedback = page.locator("section", { has: page.getByRole("heading", { name: "Tell us how it went" }) });
  await expect(page.getByRole("heading", { name: "Three looks", exact: true })).toBeFocused(); // not lost
  await tabTo(page, feedback.getByRole("radio", { name: "Yes", exact: true }), 120);
  await page.keyboard.press("Space");
  await page.keyboard.press("Tab");
  await page.keyboard.press("ArrowRight"); // usefulness 2
  await page.keyboard.press("ArrowRight"); // usefulness 3
  await page.keyboard.press("Tab");
  await page.keyboard.press("ArrowRight"); // try-on 2
  await expect(feedback.getByRole("group", { name: /How good was the try-on/ }).getByRole("radio", { name: "2" })).toBeChecked();
  await page.keyboard.press("Tab");
  const likeness = feedback.getByRole("checkbox", { name: "It did not look like me" });
  await expect(likeness).toBeFocused();
  expect(await focusRing(likeness)).toBe("solid 2px");
  await page.keyboard.press("Space");
  await expect(likeness).toBeChecked();
  const none = feedback.getByRole("checkbox", { name: "I noticed no problems" });
  await tabTo(page, none, 10);
  await page.keyboard.press("Space");
  await expect(none).toBeChecked();
  await expect(likeness).not.toBeChecked();
  const send = feedback.getByRole("button", { name: "Send feedback" });
  await tabTo(page, send, 10);
  const sent = page.waitForRequest((request) => request.url().endsWith("/api/feedback") && request.method() === "POST");
  await page.keyboard.press("Enter");
  expect((await sent).postDataJSON()).toMatchObject({
    would_wear: true,
    usefulness: 3,
    try_on: 2,
    try_on_issues: ["none_noticed"],
  });
  await expect(page.getByText("Thank you. Your feedback is saved.")).toBeFocused();
  const remove = feedback.getByRole("button", { name: /^Delete answer/ }).first();
  await page.keyboard.press("Tab");
  await expect(remove).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByText("Your feedback was deleted.")).toBeFocused();
  await shot(page, project, "09-feedback-keyboard");

  const horizontalOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
  );
  expect(horizontalOverflow).toBe(false);
});
