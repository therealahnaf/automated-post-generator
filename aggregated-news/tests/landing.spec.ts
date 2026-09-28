import { expect, test } from "@playwright/test";
import { articles } from "../src/data/articles";

const fixtures = articles.map((article, index) => ({
  id: article.id,
  title: article.title,
  description: article.description,
  workflow_type: index === 1 ? "model" : index === 4 ? "informative" : "news",
  sources: [{ label: article.sourceName, url: article.sourceUrl }],
  published_at: "2026-09-25T00:00:00Z",
  is_demo: true,
  media: [{ kind: "image", mime_type: "image/png", url: `/api/media/fixture-${index}` }],
  source_media: [],
  assets: index === 0 ? [
    { asset_type: "bundled_background", mime_type: "image/png", url: "/api/media/fixture-2" },
    { asset_type: "generated_background", mime_type: "image/png", url: "/api/media/fixture-0" },
  ] : index === 1 ? [
    { asset_type: "bundled_background", mime_type: "image/png", url: "/api/media/fixture-1" },
  ] : [],
}));

test.beforeEach(async ({ page }) => {
  await page.route("**/api/posts?*", (route) => {
    const url = new URL(route.request().url());
    const workflow = url.searchParams.get("workflow_type");
    const section = url.searchParams.get("section");
    const subset = fixtures.filter((post) =>
      workflow ? post.workflow_type === workflow :
        section === "models" ? post.workflow_type === "model" :
        section === "products" ? post.workflow_type === "product" :
        section === "thoughts" ? post.workflow_type === "informative" : true,
    );
    const offset = Number(url.searchParams.get("offset") || 0);
    const limit = Number(url.searchParams.get("limit") || 30);
    return route.fulfill({ json: { items: subset.slice(offset, offset + limit), total: subset.length } });
  });
  await page.route("**/api/posts/*", (route) => {
    const id = route.request().url().split("/").pop();
    const post = fixtures.find((item) => item.id === id);
    return post ? route.fulfill({ json: post }) : route.fulfill({ status: 404 });
  });
  await page.route("**/api/media/fixture-*", async (route) => {
    const index = Number(route.request().url().split("fixture-")[1]);
    const path = articles[index]?.image;
    if (!path) return route.abort();
    await route.fulfill({ response: await page.request.get(path) });
  });
});

test("selected tab underline meets the expanded intro panel", async ({ page }) => {
  await page.goto("/");
  for (const section of ["Home", "Models"]) {
    if (section !== "Home") await page.getByRole("link", { name: section, exact: true }).click();
    await expect(page.locator(".section-intro")).toBeVisible();
    await page.waitForTimeout(900);
    const geometry = await page.evaluate(() => {
      const tab = document.querySelector('.news-tabs [aria-current="page"]');
      const intro = document.querySelector(".section-intro");
      if (!tab || !intro) throw new Error("Missing active tab or intro panel");
      return {
        gap: intro.getBoundingClientRect().top - tab.getBoundingClientRect().bottom,
        tabWidth: tab.getBoundingClientRect().width,
        stemWidth: Number.parseFloat(getComputedStyle(intro, "::before").width),
      };
    });
    expect(geometry.gap).toBeLessThanOrEqual(1);
    expect(Math.abs(geometry.stemWidth - geometry.tabWidth)).toBeLessThanOrEqual(1);
  }
});

test("home shows the mixed feed and latest model and thought highlights", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(fixtures[0]!.title);
  await expect(page.getByRole("navigation", { name: "News sections" })).toContainText("Home");
  await expect(page.getByRole("navigation", { name: "News sections" })).toContainText("Models");
  await expect(page.getByRole("heading", { name: "Latest model release" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Tokens for Thought" })).toBeVisible();
  await expect(page.getByRole("link", { name: /View details/ }).first()).toBeVisible();
});

test("detail view uses a saved generated image as the background", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("link", { name: /View details/ }).first().click();
  await expect(page.locator(".detail-hero-art")).toBeVisible();
  await expect(page.getByRole("heading", { name: "The story" })).toBeVisible();
  await expect(page.locator(".detail-carousel")).toHaveCount(0);
});

test("generated background and original X photos appear in their separate places", async ({ page }) => {
  const post = fixtures[0]!;
  await page.route(`**/api/posts/${post.id}`, (route) => route.fulfill({
    json: {
      ...post,
      source_media: [
        { kind: "image", mime_type: "image/png", url: "/api/media/fixture-1" },
        { kind: "image", mime_type: "image/png", url: "/api/media/fixture-2" },
      ],
    },
  }));
  await page.goto(`/?post=${post.id}`);
  const carousel = page.getByRole("region", { name: "Original photos from the X post" });
  await expect(carousel).toBeVisible();
  await expect(carousel.getByRole("img")).toHaveAttribute("src", "/api/media/fixture-1");
  await expect(page.locator(".detail-hero-art img")).toHaveAttribute("src", "/api/media/fixture-0");
  await carousel.getByRole("button", { name: "Next photo" }).click();
  await expect(carousel.getByRole("img")).toHaveAttribute("src", "/api/media/fixture-2");
  await carousel.getByRole("button", { name: "Previous photo" }).click();
  await expect(carousel.getByRole("img")).toHaveAttribute("src", "/api/media/fixture-1");
});

test("X-only photos stay off listing cards and appear in the detail carousel", async ({ page }) => {
  const post = fixtures[3]!;
  await page.route(`**/api/posts/${post.id}`, (route) => route.fulfill({
    json: { ...post, source_media: [
      { kind: "image", mime_type: "image/png", url: "/api/media/fixture-3" },
    ] },
  }));
  await page.goto("/");
  const card = page.locator(".article-card").filter({ hasText: post.title });
  await expect(card.locator(".editorial-image--fallback")).toBeVisible();
  await expect(card.locator(".editorial-image img")).toHaveCount(0);
  await card.getByRole("link", { name: /View details/ }).click();
  await expect(page.locator(".detail-hero-art")).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Original photos from the X post" }).getByRole("img"))
    .toHaveAttribute("src", "/api/media/fixture-3");
});

test("section tabs and empty state work", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("navigation", { name: "News sections" }).getByRole("link", { name: "Products" }).click();
  await expect(page.getByRole("heading", { name: "No stories in this section yet." })).toBeVisible();
});

test("model listings use their archived background asset", async ({ page }) => {
  await page.goto("/?section=models");
  await expect(page.locator(".featured .editorial-image img"))
    .toHaveAttribute("src", "/api/media/fixture-1");
});

test("layout stays within the viewport", async ({ page }) => {
  for (const width of [320, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  }
});
