import { useEffect, useState } from "react";
import type { Article } from "./types";
import { toArticle, type ApiPost } from "./useArticles";

type DetailStatus = "loading" | "ready" | "not-found" | "error";

export function useArticleDetail(postId: string) {
  const [state, setState] = useState<{
    id: string;
    article: Article | null;
    status: DetailStatus;
  }>({ id: "", article: null, status: "loading" });

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const response = await fetch(`/api/posts/${encodeURIComponent(postId)}`, {
          signal: controller.signal,
        });
        if (response.status === 404 || response.status === 422) {
          setState({ id: postId, article: null, status: "not-found" });
          return;
        }
        if (!response.ok) throw new Error("Story unavailable.");
        const article = toArticle((await response.json()) as ApiPost);
        setState({ id: postId, article, status: "ready" });
      } catch {
        if (!controller.signal.aborted)
          setState({ id: postId, article: null, status: "error" });
      }
    }
    void load();
    return () => controller.abort();
  }, [postId]);

  return state.id === postId
    ? state
    : { article: null, status: "loading" as const };
}
