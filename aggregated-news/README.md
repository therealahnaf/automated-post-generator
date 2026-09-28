# The Bits Today — news frontend

React + TypeScript + Vite landing page backed by the FastAPI content service.
The browser only reads public `/api` endpoints and never receives credentials.

## Development

Use Node.js 22.13 or newer.

```sh
npm ci
npm run dev
```

Vite prints the local address. `npm run build` creates the static `dist/`
directory; `npm run preview` serves a local production-build preview.
Start the content API on port 8001 first (see `../content_api/README.md`). Vite
proxies `/api` to that service. Production hosting must route `/api` to FastAPI
on the same origin; `vite preview` alone does not supply the production API.

## Cloudflare CLI

This project pins Wrangler locally as a development dependency. Authenticate
through Cloudflare's browser approval flow; do not put Cloudflare credentials in
this repository or send passwords/API tokens in chat:

```sh
npm run cloudflare:login
npm run cloudflare:whoami
```

If a saved Wrangler grant expires, run the login command again. Authentication
alone does not deploy the site or configure a Pages project. Before deployment,
choose a Cloudflare Pages project and a production `/api` route to FastAPI;
the frontend cannot serve that Python API by itself.

## Checks

```sh
npm run lint
npm run build
npx playwright install chromium
npm test
```

Browser checks use intercepted API fixtures; the API has separate
real-PostgreSQL integration tests. Update the browser checks alongside the
new section-navigation and pagination behavior before relying on them.

## Structure and scope

- `src/useArticles.ts`: paginated API fetch, one-minute refresh, and typed view mapping.
- `src/data/articles.ts`: local seed/browser-test fixtures only, not live content.
- `src/components/`: masthead and reusable story cards.
- `src/tokens.css`: coral #FF5757, mint #C2FFE1, charcoal #212121.
- `src/styles.css`: responsive newspaper layout and CSS image treatment.
- `public/images/`: copies of existing artwork/media, with no runtime dependency
  on ignored `output/` files.

The root Home page is an editorial edition: a lead story and three-story
sidebar, this week's news, an all-time trending list, model and thought
features, a product spotlight, other stories, and reels. Trending slots
temporarily use newest-first ordering and say "Latest posts for now"; no
engagement rankings or scheduled jobs are implemented yet. The weekly news
row includes news and reels published in the previous seven days. Independent
section requests keep model, product, thought, and reel features populated even
when newer news pushes them out of the main archive page. Missing sections
show empty states, never invented articles. There is no
separate News tab. Models, Products, and Tokens for Thought map to `model`,
`product`, and `informative`.
All sections use API-backed pagination with 24 posts per page. Each card opens an individual detail
view with the full English description, a blended background, ordered
media, and source links. Feed cards and detail heroes prefer the archived raw
AI-generated background, then a selected bundled background. Original X photos
are never used in listing cards; they appear in a separate detail-page carousel,
including when a generated background is also available. Older posts without
archived backgrounds get a branded CSS fallback. Reel listings retain the video
poster frame. Polymarket is omitted from the visible source list
without modifying stored provenance. Search,
menu, and newsletter are not exposed yet. Video stories use a server-generated
still frame in listings, with playback only on their detail page. Homepage
artwork sits alongside the copy with no overlaid text; original X photos
remain exclusive to detail pages. The lead story
shows one truncated excerpt of the archived English
description, while smaller cards show shorter excerpts.
Fonts are self-hosted Roboto.

## Mock asset provenance

| Frontend asset      | Existing repository source                                                                    |
| ------------------- | --------------------------------------------------------------------------------------------- |
| `data-center.png`   | `output/china-zai-data-center-background.png` (AI illustration)                               |
| `gemini.png`        | `output/model-workflow-gemini/background.png` (AI illustration)                               |
| `unitree.jpg`       | Still at 3 seconds of `output/reel-test-unitree/source.mp4`                                   |
| `twitch.jpg`        | `output/interactive-2089546324072878355-english-v2/media/2089546324072878355-photo-1.jpg`     |
| `thoughts.png`      | `assets/fonts/images/bg-1.png`                                                                |
| `cybersecurity.png` | `output/news-polymarket-2081732508941517284/post/01-primary-background.png` (AI illustration) |

These are preview fixtures, not newly verified reporting. Confirm publication
rights and replace fixtures with the chosen read-only content API before launch.
