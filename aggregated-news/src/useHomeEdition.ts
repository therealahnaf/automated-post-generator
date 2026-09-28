import { useEffect, useState } from "react";
import type { Article } from "./types";
import { toArticle, type ApiPost } from "./useArticles";

/** Independent feeds keep specialist sections visible as the main archive grows. */
export function useHomeEdition(enabled: boolean) {
  const [edition, setEdition] = useState<{
    news: Article[];
    weekNews: Article[];
    reels: Article[];
    trending: Article[];
    popular: Article[];
    model: Article | null;
    thought: Article | null;
    product: Article | null;
    asOf: number;
  }>(() => ({ news: [], weekNews: [], reels: [], trending: [], popular: [], model: null, thought: null, product: null, asOf: Date.now() }));

  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    async function refresh() {
      const feeds = [
        ["news", "section=news&limit=12"],
        ["weekNews", `section=news&limit=100&published_since=${encodeURIComponent(new Date(Date.now() - 7 * 86400000).toISOString())}`],
        ["reels", "workflow_type=reel&limit=3"],
        ["model", "workflow_type=model&limit=1"],
        ["thought", "workflow_type=informative&limit=1"],
        ["product", "workflow_type=product&limit=1"],
      ] as const;
      // One unavailable section must not hide the rest of the edition.
      await Promise.allSettled(feeds.map(async ([key, query]) => {
        const response = await fetch(`/api/posts?${query}`, { signal: controller.signal });
        if (!response.ok) throw new Error("Section unavailable");
        const payload = await response.json() as { items: ApiPost[] };
        const articles = payload.items.map(toArticle);
        if (!controller.signal.aborted) {
          setEdition((previous) => ({
            ...previous,
            asOf: Date.now(),
            [key]: key === "news" || key === "weekNews" || key === "reels" ? articles : articles[0] ?? null,
          }));
        }
      }));
      await Promise.allSettled((["trending", "popular"] as const).map(async (key) => {
        const kind = key === "trending" ? "trending_week" : "popular_all_time";
        const response = await fetch(`/api/posts/rankings?kind=${kind}&limit=6`, { signal: controller.signal });
        if (!response.ok) throw new Error("Rankings unavailable");
        const payload = await response.json() as { items: ApiPost[] };
        if (!controller.signal.aborted) setEdition((previous) => ({ ...previous, [key]: payload.items.map(toArticle) }));
      }));
    }
    void refresh();
    const interval = window.setInterval(() => void refresh(), 60000);
    return () => {
      controller.abort();
      window.clearInterval(interval);
    };
  }, [enabled]);

  return edition;
}
