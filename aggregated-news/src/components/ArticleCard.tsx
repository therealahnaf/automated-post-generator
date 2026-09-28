import type { Article } from "../types";

function publicationDate(article: Article): string {
  if (!article.publishedAt) return "The Bits Today";
  return new Date(article.publishedAt).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function leadExcerpt(text: string): string {
  const paragraph = (text.split(/\n\s*\n/)[0] || text).replace(/\s+/g, " ").trim();
  if (paragraph.length <= 260) return paragraph;
  const end = paragraph.lastIndexOf(" ", 260);
  return `${paragraph.slice(0, end > 0 ? end : 260).trimEnd()}…`;
}

function ViewDetails({
  article,
  onOpen,
  prominent = false,
}: {
  article: Article;
  onOpen: (postId: string) => void;
  prominent?: boolean;
}) {
  const url = new URL(window.location.href);
  url.searchParams.set("post", article.id);
  return (
    <a
      className={prominent ? "detail-link detail-link--prominent" : "detail-link"}
      href={`${url.pathname}${url.search}`}
      onClick={(event) => {
        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        onOpen(article.id);
      }}
    >
      View details <span aria-hidden="true">→</span>
    </a>
  );
}

export function EditorialImage({
  article,
  hero = false,
}: {
  article: Article;
  hero?: boolean;
}) {
  return (
    <div
      className={`editorial-image ${hero ? "editorial-image--hero" : ""} ${article.imageTreatment === "published" ? "editorial-image--published" : ""} ${!article.image ? "editorial-image--fallback" : ""}`}
    >
      {article.image && <img
        src={article.image}
        alt={article.imageAlt}
        loading={hero ? "eager" : "lazy"}
        fetchPriority={hero ? "high" : "auto"}
      />}
    </div>
  );
}

export function FeaturedArticle({ article, onOpen }: { article: Article; onOpen: (postId: string) => void }) {
  return (
    <article
      className={`featured panel ${article.imageTreatment === "published" ? "featured--published" : ""}`}
      id="front-page"
      aria-labelledby="lead-headline"
    >
      <h1 id="lead-headline">{article.title}</h1>
      <div className="featured-copy">
        <p>{leadExcerpt(article.description)}</p>
      </div>
      <ViewDetails article={article} onOpen={onOpen} prominent />
      <figure className="featured-figure">
        <EditorialImage article={article} hero />
      </figure>
    </article>
  );
}

export function ArticleCard({
  article,
  variant = "compact",
  onOpen,
}: {
  article: Article;
  variant?: "compact" | "bottom" | "search";
  onOpen: (postId: string) => void;
}) {
  return (
    <article className={`article-card article-card--${variant}`}>
      <div className="article-card-copy">
        <p className="eyebrow">
          <span className="square" aria-hidden="true" />
          {article.sourceName}
        </p>
        <h3>{article.title}</h3>
        <p className="article-summary">{article.summary}</p>
        <div className="article-card-bottom">
          <ViewDetails article={article} onOpen={onOpen} />
        </div>
      </div>
      <span className="story-meta">{publicationDate(article)}{article.mediaKind === "video" ? " · Video" : ""}</span>
      <div className="article-image-link">
        <EditorialImage article={article} />
      </div>
    </article>
  );
}
