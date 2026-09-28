import { useEffect, useState } from "react";
import { useArticleDetail } from "../useArticleDetail";
import type { Article } from "../types";
import { EditorialImage } from "./ArticleCard";

function visibleSource(source: Article["sources"][number]): boolean {
  return !/polymarket/i.test(source.label) &&
    !/(?:polymarket\.com|(?:x|twitter)\.com\/polymarket(?:\/|$))/i.test(source.url || "");
}

function MediaItem({
  item,
  title,
  index,
  total,
}: {
  item: NonNullable<Article["media"]>[number];
  title: string;
  index: number;
  total: number;
}) {
  return (
    <figure className={`detail-media panel ${item.kind === "video" ? "detail-media--video" : ""}`}>
      {item.kind === "video" ? (
        <video
          src={item.url}
          poster={item.poster_url || undefined}
          controls
          playsInline
          preload="metadata"
          aria-label={`Video for ${title}`}
        />
      ) : (
        <img src={item.url} alt={`${title}, visual ${index} of ${total}`} loading={index === 1 ? "eager" : "lazy"} />
      )}
      <figcaption>
        <span>{String(index).padStart(2, "0")} / {String(total).padStart(2, "0")}</span>
        <span>{item.kind === "video" ? "Video" : "Image"}</span>
      </figcaption>
    </figure>
  );
}

function SourceCarousel({ images, title }: { images: NonNullable<Article["sourceMedia"]>; title: string }) {
  const [active, setActive] = useState(0);
  const current = Math.min(active, images.length - 1);
  return (
    <section className="detail-carousel" aria-label="Story photos">
      <div className="detail-carousel-heading">
        <span>{String(current + 1).padStart(2, "0")} / {String(images.length).padStart(2, "0")}</span>
      </div>
      <div className="detail-carousel-stage panel">
        <img src={images[current]!.url} alt={`${title}, photo ${current + 1} of ${images.length}`} />
        {images.length > 1 && <div className="detail-carousel-controls">
          <button type="button" onClick={() => setActive((current - 1 + images.length) % images.length)} aria-label="Previous photo">←</button>
          <button type="button" onClick={() => setActive((current + 1) % images.length)} aria-label="Next photo">→</button>
        </div>}
      </div>
      {images.length > 1 && <div className="detail-carousel-dots" aria-label="Choose a photo">
        {images.map((image, index) => <button key={`${image.url}-${index}`} type="button" aria-label={`Photo ${index + 1}`} aria-current={index === current ? "true" : undefined} onClick={() => setActive(index)} />)}
      </div>}
    </section>
  );
}

export function ArticleDetail({
  postId,
  backHref,
  backLabel,
  onBack,
}: {
  postId: string;
  backHref: string;
  backLabel: string;
  onBack: () => void;
}) {
  const { article, status } = useArticleDetail(postId);
  useEffect(() => {
    if (article) document.title = `${article.title} | The Bits Today`;
  }, [article]);

  const backLink = (
    <a
      className="detail-back"
      href={backHref}
      onClick={(event) => {
        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        onBack();
      }}
    >
      <span aria-hidden="true">←</span> Back to {backLabel}
    </a>
  );

  if (status !== "ready" || !article) {
    return (
      <section className="detail-state panel" role={status === "error" ? "alert" : "status"}>
        {backLink}
        <h1>
          {status === "loading" ? "Loading story…" : status === "not-found" ? "Story not found" : "Story unavailable"}
        </h1>
        {status !== "loading" && <p>Return to the archive to explore other stories.</p>}
      </section>
    );
  }

  const media = article.media || [];
  const originalPhotos = article.sourceMedia || [];
  const date = article.publishedAt
    ? new Date(article.publishedAt).toLocaleDateString("en-US", {
        month: "long",
        day: "numeric",
        year: "numeric",
      })
    : null;
  const paragraphs = article.description.split(/\n\s*\n/).map((text) => text.trim()).filter(Boolean);
  const sources = article.sources.filter(visibleSource);
  const textOnly = !article.image && ["model", "product", "informative"].includes(article.workflowType);
  const showGallery = !textOnly && originalPhotos.length === 0 && media.length > 1;
  const hasSidebar = originalPhotos.length > 0 || media[0]?.kind === "video" || showGallery || sources.length > 0;
  const visualCount = originalPhotos.length || (article.image || media[0]?.kind === "video" ? 1 : 0);

  return (
    <article className="story-detail">
      <div className="detail-topline">
        {backLink}
      </div>
      <div className={`detail-hero panel${textOnly ? " detail-hero--text" : ""}`}>
        {article.image && media[0]?.kind !== "video" && <div className="detail-hero-art" aria-hidden="true"><EditorialImage article={article} /></div>}
        <header className="detail-header">
          <p className="eyebrow"><span className="square" aria-hidden="true" />{article.sourceName}{article.mediaKind === "video" ? " · Video" : ""}</p>
          <h1>{article.title}</h1>
          <div className="detail-meta">
            {date && <time dateTime={article.publishedAt}>{date}</time>}
            {visualCount > 0 && <><span aria-hidden="true">/</span><span>{visualCount} {visualCount === 1 ? "visual" : "visuals"}</span></>}
          </div>
        </header>
      </div>

      <div className={`detail-body ${hasSidebar ? "" : "detail-body--single"}`}>
        <section className="detail-copy panel" aria-label="Full story">
          <h2>The story</h2>
          {paragraphs.map((paragraph, index) => <p key={index}>{paragraph}</p>)}
        </section>
        {hasSidebar && <aside className="detail-aside" aria-label="Story media and sources">
          {originalPhotos.length > 0 && <SourceCarousel key={article.id} images={originalPhotos} title={article.title} />}
          {media[0]?.kind === "video" && <section className="detail-watch" aria-label="Watch the video">
            <h2>Watch the video</h2>
            <MediaItem item={media[0]} title={article.title} index={1} total={media.length} />
          </section>}
          {showGallery && <section className="detail-gallery" aria-label="More media from this story">
            <h2>More from this story</h2>
            <div className="detail-gallery-grid">{media.slice(1).map((item, index) => (
              <MediaItem key={`${item.url}-${index}`} item={item} title={article.title} index={index + 2} total={media.length} />
            ))}</div>
          </section>}
          {sources.length > 0 && <div className="detail-sources panel">
            <h2>Sources</h2>
            <ol>{sources.map((source, index) => (
              <li key={`${source.label}-${index}`}>
                {source.url ? <a href={source.url} target="_blank" rel="noopener noreferrer">{source.label}<span aria-hidden="true">↗</span></a> : <span>{source.label}</span>}
              </li>
            ))}</ol>
          </div>}
        </aside>}
      </div>
    </article>
  );
}
