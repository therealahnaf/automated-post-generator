import { expect, test } from "@playwright/test";
import { articles } from "../src/data/articles";
import { toArticle, type ApiPost } from "../src/useArticles";

const fixtures = articles.map((article, index) => ({
  id: article.id,
  title: article.title,
  description: article.description,
  workflow_type: index === 1 ? "model" : index === 4 ? "informative" : "news",
  sources: [{ label: article.sourceName, url: article.sourceUrl }],
  published_at: new Date(Date.now() - index * 3600000).toISOString(),
  is_demo: true,
  media: [{ kind: "image", mime_type: "image/png", url: `/api/media/fixture-${index}` }],
  source_media: index === 3 ? [{ kind: "image", mime_type: "image/png", url: "/api/media/fixture-3" }] : [],
  assets: index === 0 ? [
    { asset_type: "bundled_background", mime_type: "image/png", url: "/api/media/fixture-2" },
    { asset_type: "generated_background", mime_type: "image/png", url: "/api/media/fixture-0" },
  ] : index === 1 ? [
    { asset_type: "bundled_background", mime_type: "image/png", url: "/api/media/fixture-1" },
  ] : [],
}));

test.beforeEach(async ({ page }) => {
  await page.route("**/pagead/js/adsbygoogle.js?*", (route) => route.fulfill({ contentType: "application/javascript", body: "" }));
  await page.route("**/api/posts?*", (route) => {
    const url = new URL(route.request().url());
    const workflow = url.searchParams.get("workflow_type");
    const section = url.searchParams.get("section");
    const subset = fixtures.filter((post) =>
      workflow ? post.workflow_type === workflow :
        section === "news" ? ["news", "reel"].includes(post.workflow_type) :
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

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: "ignoreErrors" });
});

test("selected tab underline meets the expanded intro panel", async ({ page }) => {
  await page.goto("/?section=models");
  for (const section of ["Models", "Products"]) {
    if (section !== "Models") await page.getByRole("navigation", { name: "News sections" }).getByRole("link", { name: section, exact: true }).click();
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
  await expect(page.getByRole("heading", { name: "Model releases" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Tokens for Thought" })).toBeVisible();
  await expect(page.getByRole("link", { name: /View full story/ })).toBeVisible();
  for (const name of ["Trending this week", "This week’s news", "All-time trending", "Other stories", "Reels"]) {
    await expect(page.getByRole("heading", { name, exact: true })).toBeVisible();
  }
  await expect(page.locator("video")).toHaveCount(0);
});

test("the final top container is an ad on home, category, and detail pages", async ({ page }) => {
  await page.goto("/");
  const script = page.locator('script[src*="pagead/js/adsbygoogle.js"]');
  await expect(script).toHaveCount(1);
  await expect(script).toHaveAttribute("src", /ca-pub-8059416875418410/);
  const homeAd = page.locator(".edition-top-side > .ad-card:last-child");
  await expect(homeAd).toBeVisible();
  await expect(homeAd.locator("ins.adsbygoogle")).toHaveAttribute("data-ad-slot", "8468894754");
  await expect(page.locator(".edition-top-side .edition-card")).toHaveCount(2);
  await homeAd.locator("ins.adsbygoogle").evaluate((ad) => {
    const iframe = document.createElement("iframe");
    iframe.style.height = "280px";
    iframe.style.width = "100%";
    iframe.style.border = "0";
    ad.append(iframe);
  });
  const homeAdBottom = await homeAd.evaluate((ad) => ad.getBoundingClientRect().bottom);
  const adContentBottom = await homeAd.locator("ins.adsbygoogle").evaluate((ad) => ad.getBoundingClientRect().bottom);
  const adFrameBottom = await homeAd.locator("iframe").evaluate((frame) => frame.getBoundingClientRect().bottom);
  const nextSectionTop = await page.getByRole("region", { name: "This week’s news" }).evaluate((section) => section.getBoundingClientRect().top);
  expect(adContentBottom).toBeLessThanOrEqual(homeAdBottom + 1);
  expect(adFrameBottom).toBeLessThanOrEqual(adContentBottom + 1);
  expect(homeAdBottom).toBeLessThanOrEqual(nextSectionTop + 1);

  await page.getByRole("navigation", { name: "News sections" }).getByRole("link", { name: "Models", exact: true }).click();
  await expect(page.locator(".other-news > .ad-card:last-child")).toBeVisible();
  await expect(page.locator("main .ad-card")).toHaveCount(1);

  await page.getByRole("link", { name: "View details" }).first().click();
  await expect(page.locator(".detail-aside > .ad-card:last-child")).toBeVisible();
  await expect(page.locator("main .ad-card")).toHaveCount(1);
});

test("ad loading and unfilled responses do not shift the layout at any breakpoint", async ({ page }) => {
  for (const width of [320, 390, 480, 481, 768, 800, 801, 1024, 1440]) {
    await page.setViewportSize({ width, height: 1050 });
    await page.goto("/");
    const ad = page.locator(".ad-card--side");
    await expect(ad).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    await page.waitForFunction(() => [...document.images].every((image) => image.complete));
    const measure = () => page.evaluate(() => {
      const card = document.querySelector(".ad-card--side")!.getBoundingClientRect();
      const next = document.querySelector('[aria-label="This week’s news"]')!.getBoundingClientRect();
      const frame = document.querySelector(".ad-card-frame")!.getBoundingClientRect();
      return { x: card.x, y: card.y, width: card.width, height: card.height, nextTop: next.top, frameHeight: frame.height, pageWidth: document.documentElement.scrollWidth };
    });
    const before = await measure();
    expect(before.frameHeight).toBe(280);
    expect(before.pageWidth).toBe(width);
    expect(before.x + before.width).toBeLessThanOrEqual(width);
    const unit = ad.locator("ins");
    expect(await unit.getAttribute("data-ad-format")).toBeNull();
    expect(await unit.getAttribute("data-full-width-responsive")).toBeNull();
    // Simulate an async fill at the requested height, then a smaller creative,
    // and finally an unfilled response. The reservation must survive all three.
    await unit.evaluate((element) => {
      const iframe = document.createElement("iframe");
      iframe.style.cssText = "display:block;width:100%;height:280px;border:0";
      element.append(iframe);
      element.setAttribute("data-ad-status", "filled");
    });
    expect(await measure()).toEqual(before);
    await unit.evaluate((element) => {
      element.style.height = "250px";
      element.querySelector("iframe")!.style.height = "250px";
    });
    expect(await measure()).toEqual(before);
    await unit.evaluate((element) => {
      element.replaceChildren();
      element.style.height = "0px";
      element.setAttribute("data-ad-status", "unfilled");
    });
    expect(await measure()).toEqual(before);
  }
});

test("category and detail ads reserve their dimensions before requesting a creative", async ({ page }) => {
  await page.addInitScript(() => {
    const requests: { width: number; height: number }[] = [];
    Object.assign(window, {
      adRequests: requests,
      adsbygoogle: { push: () => {
        const unit = document.querySelector("ins.adsbygoogle")!;
        const rect = unit.getBoundingClientRect();
        requests.push({ width: rect.width, height: rect.height });
      } },
    });
  });
  for (const width of [320, 768, 1024, 1440]) {
    await page.setViewportSize({ width, height: 1050 });
    for (const url of ["/?section=models", `/?post=${fixtures[0]!.id}`]) {
      await page.goto(url);
      const ad = page.locator("main .ad-card");
      await expect(ad).toBeVisible();
      await page.evaluate(() => document.fonts.ready);
      const before = await ad.boundingBox();
      const requests = await page.evaluate(() => (window as Window & { adRequests: { width: number; height: number }[] }).adRequests);
      expect(requests).toHaveLength(1);
      expect(requests[0]!.height).toBe(280);
      expect(requests[0]!.width).toBeGreaterThan(0);
      expect(requests[0]!.width).toBeLessThan(width);
      await ad.locator("ins").evaluate((unit) => {
        const iframe = document.createElement("iframe");
        iframe.style.cssText = "display:block;width:100%;height:280px;border:0";
        unit.append(iframe);
      });
      expect(await ad.boundingBox()).toEqual(before);
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
    }
  }
});

test("home uses insight rankings when available", async ({ page }) => {
  const ranked = fixtures.find((post) => post.workflow_type === "news" && post.id !== fixtures[0]!.id)!;
  await page.route("**/api/posts/rankings?*", (route) => route.fulfill({
    json: { items: [ranked], total: 1, kind: "trending_week" },
  }));
  await page.goto("/");
  await expect(page.locator(".edition-lead h1")).toHaveText(ranked.title);
  await expect(page.locator(".edition-top-side .edition-card")).toHaveCount(2);
  await expect(page.locator(".edition-top").getByRole("heading", { name: ranked.title })).toHaveCount(1);
  await expect(page.locator(".edition-ranked li")).toHaveCount(4);
  await expect(page.getByText(/Instagram engagement/)).toHaveCount(0);
});

test("detail view uses a saved generated image as the background", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("link", { name: /View full story/ }).click();
  await expect(page.locator(".detail-hero-art")).toBeVisible();
  await expect(page.getByRole("heading", { name: "The story" })).toBeVisible();
  await expect(page.locator(".detail-carousel")).toHaveCount(0);
});

test("generated background and original X photos appear in their separate places", async ({ page }) => {
  const post = fixtures[0]!;
  await page.route(`**/api/posts/${post.id}`, (route) => route.fulfill({
    json: {
      ...post,
      sources: [{ label: "Research", url: "https://example.com/story" }],
      source_media: [
        { kind: "image", mime_type: "image/png", url: "/api/media/fixture-1" },
        { kind: "image", mime_type: "image/png", url: "/api/media/fixture-2" },
      ],
    },
  }));
  await page.goto(`/?post=${post.id}`);
  const carousel = page.getByRole("region", { name: "Story photos" });
  await expect(carousel).toBeVisible();
  await expect(page.getByText("Photos from the original post")).toHaveCount(0);
  await expect(page.locator(".detail-aside .detail-carousel")).toBeVisible();
  await expect(page.locator(".detail-aside .detail-sources")).toBeVisible();
  await expect(carousel.getByRole("img")).toHaveAttribute("src", "/api/media/fixture-1");
  await expect(page.locator(".detail-hero-art img")).toHaveAttribute("src", "/api/media/fixture-0");
  await carousel.getByRole("button", { name: "Next photo" }).click();
  await expect(carousel.getByRole("img")).toHaveAttribute("src", "/api/media/fixture-2");
  await carousel.getByRole("button", { name: "Previous photo" }).click();
  await expect(carousel.getByRole("img")).toHaveAttribute("src", "/api/media/fixture-1");
});

test("news with an X photo uses it as the listing thumbnail and keeps it in the detail carousel", async ({ page }) => {
  const post = fixtures[3]!;
  await page.goto("/");
  const card = page.locator(".edition-card").filter({ hasText: post.title }).first();
  await expect(card.locator(".edition-art img")).toHaveAttribute("src", "/api/media/fixture-3");
  await card.getByRole("heading").getByRole("link").click();
  await expect(page.locator(".detail-hero-art")).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Story photos" }).getByRole("img"))
    .toHaveAttribute("src", "/api/media/fixture-3");
});

test("news with a template and X photo prefers the photo only for thumbnails", () => {
  const post = {
    ...fixtures[3]!,
    assets: [{ asset_type: "bundled_background", mime_type: "image/png", url: "/api/media/fixture-2" }],
  } as ApiPost;
  const article = toArticle(post);
  expect(article.thumbnailImage).toBe("/api/media/fixture-3");
  expect(article.image).toBe("/api/media/fixture-2");
  const generated = toArticle({ ...post, assets: [
    { asset_type: "generated_background", mime_type: "image/png", url: "/api/media/fixture-0" },
    ...post.assets!,
  ] });
  expect(generated.thumbnailImage).toBeNull();
  expect(generated.image).toBe("/api/media/fixture-0");
});

test("section tabs and empty state work", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("navigation", { name: "News sections" }).getByRole("link", { name: "Products" }).click();
  await expect(page.getByRole("heading", { name: "No stories in this section yet." })).toBeVisible();
  await expect(page.locator("main .ad-card")).toHaveCount(0);
});

test("model and thought cards stay text-led when only template backgrounds exist", async ({ page }) => {
  await page.goto("/");
  for (const label of ["Model releases", "Tokens for Thought"]) {
    const section = page.getByRole("region", { name: label, exact: true });
    await expect(section.locator(".edition-card--text")).toBeVisible();
    await expect(section.locator(".edition-card-art")).toHaveCount(0);
  }
  await expect(page.getByRole("region", { name: "Model releases", exact: true }).locator(".edition-card--text"))
    .toHaveCSS("background-image", /fixture-1/);
  await expect(page.getByRole("region", { name: "Tokens for Thought", exact: true }).locator(".edition-card--text"))
    .toHaveCSS("background-image", /editorial-placeholder\.png/);
  await page.goto("/?section=models");
  await expect(page.locator(".featured--text")).toBeVisible();
  await expect(page.locator(".featured-figure")).toHaveCount(0);
  await expect(page.locator(".featured--text")).toHaveCSS("background-image", /fixture-1/);
  await page.getByRole("link", { name: "View details" }).first().click();
  await expect(page.locator(".detail-hero--text")).toBeVisible();
  await expect(page.locator(".detail-hero-art")).toHaveCount(0);
  await expect(page.locator(".detail-gallery")).toHaveCount(0);
});

test("a genuinely generated model image remains visible", async ({ page }) => {
  const model = fixtures[1]!;
  await page.route("**/api/posts?*", (route) => {
    const url = new URL(route.request().url());
    if (url.searchParams.get("section") === "models") return route.fulfill({ json: {
      items: [{ ...model, assets: [{ asset_type: "generated_background", mime_type: "image/png", url: "/api/media/fixture-1" }] }], total: 1,
    } });
    return route.fallback();
  });
  await page.goto("/?section=models");
  await expect(page.locator(".featured .editorial-image img")).toHaveAttribute("src", "/api/media/fixture-1");
});

test("layout stays within the viewport", async ({ page }) => {
  for (const width of [320, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  }
});

test("weekly news excludes old stories and reels open in the detail page", async ({ page }) => {
  const old = { ...fixtures[0]!, published_at: "2020-01-01T00:00:00Z" };
  const reel = { ...fixtures[2]!, workflow_type: "reel", media: [
    { kind: "video", mime_type: "video/mp4", url: "/api/media/video", poster_url: "/api/media/fixture-2" },
  ] };
  await page.route("**/api/posts?*", (route) => {
    const url = new URL(route.request().url());
    const workflow = url.searchParams.get("workflow_type");
    const items = workflow === "reel" ? [reel] : url.searchParams.get("section") === "news" ? [old] : workflow ? [] : [old, reel];
    return route.fulfill({ json: { items, total: items.length } });
  });
  await page.route(`**/api/posts/${reel.id}`, (route) => route.fulfill({ json: reel }));
  await page.goto("/");
  await expect(page.getByText("No news published in the past seven days.", { exact: false })).toBeVisible();
  const reels = page.getByRole("region", { name: "Reels", exact: true });
  await expect(reels.locator("img")).toHaveAttribute("src", "/api/media/fixture-2");
  await expect(reels.locator(".edition-play svg")).toBeVisible();
  await expect(reels.getByText("\u25B6")).toHaveCount(0);
  await expect(page.locator("video")).toHaveCount(0);
  await reels.getByRole("heading", { level: 3 }).getByRole("link").click();
  await expect(page.locator("video")).toBeVisible();
});

test("newsletter signup collects an email and confirms without promising delivery", async ({ page }) => {
  let submitted = "";
  await page.route("**/api/newsletter/subscriptions", async (route) => {
    submitted = route.request().postDataJSON().email;
    await route.fulfill({ json: { status: "accepted" } });
  });
  await page.goto("/");
  const signup = page.getByRole("complementary", { name: "Newsletter" });
  await expect(signup.getByRole("link", { name: "Privacy policy" })).toHaveCount(0);
  await signup.getByRole("textbox", { name: "Email address" }).fill("reader@example.com");
  await signup.getByRole("button", { name: "Join the list" }).click();
  await expect(signup.getByRole("status")).toContainText("newsletter hasn't launched yet");
  expect(submitted).toBe("reader@example.com");
});

test("archive pagination and browser back preserve navigation", async ({ page }) => {
  const many = Array.from({ length: 26 }, (_, index) => ({ ...fixtures[0]!, id: `story-${index}`, title: `Story number ${index}` }));
  await page.route("**/api/posts?*", (route) => {
    const url = new URL(route.request().url());
    const offset = Number(url.searchParams.get("offset") || 0);
    const limit = Number(url.searchParams.get("limit") || 24);
    const items = url.searchParams.has("workflow_type") ? [] : many.slice(offset, offset + limit);
    return route.fulfill({ json: { items, total: many.length } });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Story number 24");
  await expect(page.getByText("Page 2 of 2")).toBeVisible();
  await expect(page.getByRole("button", { name: "Next", exact: true })).toBeDisabled();
  await page.goBack();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Story number 0");
});
