/** Visual verification + E2E navigation over the built app in mock mode.
 * Captures the required screenshot matrix at 375/768/1440 widths. */
import { expect, test } from "@playwright/test";

const MOCK = "/activity/?mock=1&e2e=1";

const VIEWPORTS = [
  { name: "mobile", width: 375, height: 812 },
  { name: "tablet", width: 768, height: 1024 },
  { name: "desktop", width: 1440, height: 900 },
];

const SCREENS: { slug: string; hash: string; waitFor: string }[] = [
  { slug: "player-overview", hash: "grimoire/overview", waitFor: "text=Daybell" },
  { slug: "player-skills", hash: "grimoire/skills", waitFor: "text=ศาสตร์เวท" },
  { slug: "player-spellbook", hash: "grimoire/spellbook", waitFor: "text=Save DC" },
  { slug: "player-story", hash: "grimoire/story", waitFor: "text=สิ่งที่ค้นพบ" },
  { slug: "player-chronicle", hash: "grimoire/chronicle", waitFor: "text=บันทึกการเดินทาง" },
  { slug: "dm-command-center", hash: "studio/command", waitFor: "text=แรงกดดันของโลก" },
  { slug: "dm-scene", hash: "studio/scene", waitFor: "text=สถานะฉากปัจจุบัน" },
  { slug: "dm-secrets", hash: "studio/secrets", waitFor: "text=DM เท่านั้น" },
];

for (const vp of VIEWPORTS) {
  for (const s of SCREENS) {
    test(`${s.slug} @ ${vp.name}`, async ({ page }) => {
      await page.setViewportSize({ width: vp.width, height: vp.height });
      await page.goto(`${MOCK}#/${s.hash}`);
      await page.waitForSelector(s.waitFor);
      await page.waitForTimeout(400); // let skeletons settle
      // No horizontal overflow at any width.
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow, `horizontal overflow on ${s.slug} @ ${vp.name}`).toBeLessThanOrEqual(1);
      await page.screenshot({
        path: `e2e/shots/${s.slug}--${vp.name}.png`, fullPage: vp.name !== "mobile",
      });
    });
  }
}

test("dm npc detail @ desktop", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`${MOCK}#/studio/npcs`);
  await page.getByText("Mother Veyra").click();
  await page.waitForSelector("text=สิ่งที่ NPC นี้รู้");
  await page.screenshot({ path: "e2e/shots/dm-npc-detail--desktop.png" });
});

test("mobile bottom navigation works", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto(`${MOCK}#/grimoire/overview`);
  await page.waitForSelector("text=Daybell");
  const nav = page.getByRole("navigation", { name: "เมนูล่าง" });
  await expect(nav).toBeVisible();
  await nav.getByRole("button", { name: /คาถา/ }).click();
  await page.waitForSelector("text=Save DC");
  await page.screenshot({ path: "e2e/shots/mobile-bottomnav--mobile.png" });
});

test("loading and outside-discord states", async ({ page }) => {
  await page.setViewportSize({ width: 768, height: 1024 });
  // Without mock/e2e params the app shows the outside-Discord fallback.
  await page.goto("/activity/");
  await page.waitForSelector("text=Reverie Grimoire");
  await page.screenshot({ path: "e2e/shots/state-outside--tablet.png" });
});

test("full player + dm navigation journey", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`${MOCK}#/grimoire/overview`);
  await page.waitForSelector("text=Daybell");
  for (const view of ["ทักษะ", "คาถา", "ความสามารถ", "สัมภาระ", "เรื่องราว", "ปาร์ตี้", "บันทึก"]) {
    await page.getByRole("navigation", { name: "เมนูหลัก" })
      .getByRole("button", { name: view }).click();
    await page.waitForTimeout(350);
  }
  // Switch to DM Studio (owner in mock context).
  await page.getByTestId("topbar-switch").click();
  await page.waitForSelector("text=แรงกดดันของโลก");
  for (const view of ["ฉากปัจจุบัน", "โลก", "NPC", "ภัยคุกคาม", "ความลับ", "เหตุการณ์", "นำเข้า"]) {
    await page.getByRole("navigation", { name: "เมนูหลัก" })
      .getByRole("button", { name: view, exact: false }).first().click();
    await page.waitForTimeout(350);
  }
  await page.waitForSelector("text=การนำเข้าแคมเปญ");
});
