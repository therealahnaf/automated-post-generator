import { useEffect, useState } from "react";
import type { Article } from "./types";
import { toArticle, type ApiPost } from "./useArticles";

export function useHomeHighlights(enabled: boolean) {
  const [highlights, setHighlights] = useState<{
    model: Article | null;
    thought: Article | null;
  }>({ model: null, thought: null });

  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    async function refresh() {
      try {
        const fetchLatest = async (workflow: "model" | "informative") => {
          const response = await fetch(`/api/posts?workflow_type=${workflow}&limit=1`, {
            signal: controller.signal,
          });
          if (!response.ok) throw new Error("Highlights unavailable.");
          const payload = (await response.json()) as { items: ApiPost[] };
          return payload.items[0] ? toArticle(payload.items[0]) : null;
        };
        const [model, thought] = await Promise.all([
          fetchLatest("model"),
          fetchLatest("informative"),
        ]);
        if (!controller.signal.aborted) setHighlights({ model, thought });
      } catch {
        // The archive feed remains usable if highlight requests fail.
      }
    }
    void refresh();
    const interval = window.setInterval(() => void refresh(), 60000);
    return () => {
      controller.abort();
      window.clearInterval(interval);
    };
  }, [enabled]);

  return highlights;
}
