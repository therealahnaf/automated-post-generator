import { useEffect, useState } from "react";
import type { Article } from "./types";
import type { Section } from "./sections";

export const PAGE_SIZE = 24;

export interface ApiPost {
  id: string;
  title: string;
  description: string;
  workflow_type: "news" | "model" | "product" | "informative" | "reel";
  sources: { label: string; url: string | null }[];
  publication_url?: string | null;
  published_at: string;
  is_demo: boolean;
  media: { kind: "image" | "video"; url: string; poster_url?: string | null; mime_type: string }[];
  source_media?: { kind: "image"; url: string; mime_type: string }[];
  assets?: { asset_type: "x_photo" | "generated_background" | "bundled_background"; url: string; mime_type: string }[];
}
const labels = {
  news: "News",
  model: "Model release",
  product: "Product release",
  informative: "Tokens for thought",
  reel: "News",
};

function excerpt(text: string, limit: number): string {
  const paragraph = text.split(/\n\s*\n/)[0] || text;
  const firstComma = paragraph.indexOf(", ");
  if (firstComma >= 60 && firstComma <= limit) {
    return `${paragraph.slice(0, firstComma)}.`;
  }
  if (paragraph.length <= limit) return paragraph;
  const end = paragraph.lastIndexOf(" ", limit);
  return paragraph.slice(0, end > 0 ? end : limit).trimEnd() + "…";
}

export function toArticle(post: ApiPost): Article {
  const first = post.media[0];
  if (!first || !first.url.startsWith("/api/media/"))
    throw new Error("Invalid post media.");
  const background = post.assets?.find((asset) => asset.asset_type === "generated_background")
    || post.assets?.find((asset) => asset.asset_type === "bundled_background");
  const preview = background?.url || (first.kind === "video" ? first.poster_url : null);
  if (preview && !preview.startsWith("/api/media/"))
    throw new Error("Invalid post background.");
  return {
    id: post.id,
    workflowType: post.workflow_type,
    title: post.title,
    description: post.description,
    summary: excerpt(post.description, 115),
    sourceName: labels[post.workflow_type],
    sourceUrl: post.sources.find((source) => source.url)?.url || "",
    sources: post.sources,
    publicationUrl: post.publication_url,
    image: preview || null,
    imageAlt: first.kind === "video" && !background ? `Preview frame for ${post.title}` : `Editorial background for ${post.title}`,
    imageCaption: `${labels[post.workflow_type]} / The Bits Today`,
    imageTreatment: "published",
    mediaKind: first.kind,
    media: post.media,
    sourceMedia: post.source_media || [],
    readingMinutes: Math.max(
      1,
      Math.ceil(post.description.split(/\s+/).length / 220),
    ),
    publishedAt: post.published_at,
    isDemo: post.is_demo,
  };
}

export function useArticles(section: Section, page: number) {
  const queryKey = `${section}:${page}`;
  const [state, setState] = useState<{
    key: string;
    articles: Article[];
    total: number;
    status: "loading" | "ready" | "error";
  }>({ key: "", articles: [], total: 0, status: "loading" });
  useEffect(() => {
    let disposed = false;
    const controller = new AbortController();
    async function refresh() {
      try {
        const params = new URLSearchParams({
          limit: String(PAGE_SIZE),
          offset: String((page - 1) * PAGE_SIZE),
        });
        if (section !== "home") params.set("section", section);
        const response = await fetch(`/api/posts?${params}`, {
          signal: controller.signal,
        });
        if (!response.ok) throw new Error("Content unavailable.");
        const data = (await response.json()) as { items: ApiPost[]; total: number };
        if (!Array.isArray(data.items))
          throw new Error("Invalid content response.");
        const articles = data.items.map(toArticle);
        if (!disposed) setState({ key: queryKey, articles, total: data.total, status: "ready" });
      } catch {
        if (!disposed)
          setState((previous) => ({ ...previous, status: "error" }));
      }
    }
    void refresh();
    const interval = window.setInterval(() => void refresh(), 60000);
    return () => {
      disposed = true;
      controller.abort();
      window.clearInterval(interval);
    };
  }, [page, queryKey, section]);
  return state.key === queryKey
    ? state
    : { articles: [], total: 0, status: "loading" as const };
}
