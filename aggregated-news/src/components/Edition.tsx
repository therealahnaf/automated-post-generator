import type { ReactNode } from "react";
import type { Article } from "../types";
import { sectionHref, type Section } from "../sections";
import type { useHomeEdition } from "../useHomeEdition";

type OpenStory = (id: string) => void;

function StoryLink({ article, onOpen, children, className }: {
  article: Article; onOpen: OpenStory; children: ReactNode; className?: string;
}) {
  const url = new URL(window.location.href);
  url.searchParams.set("post", article.id);
  return <a className={className} href={`${url.pathname}${url.search}`} onClick={(event) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    onOpen(article.id);
  }}>{children}</a>;
}

function DateLine({ article }: { article: Article }) {
  return <div className="edition-meta">
    {article.publishedAt && <time dateTime={article.publishedAt}>{new Date(article.publishedAt).toLocaleDateString("en-US", {
      month: "short", day: "numeric", year: "numeric",
    })}</time>}
    <span>{article.readingMinutes} min read</span>
  </div>;
}

function Artwork({ article, priority = false }: { article: Article; priority?: boolean }) {
  return <div className={`edition-art ${article.image ? "" : "edition-art--fallback"}`}>
    {article.image
      ? <img src={article.image} alt={article.imageAlt} loading={priority ? "eager" : "lazy"} fetchPriority={priority ? "high" : "auto"} />
      : <><img src="/images/bits-today-logo.png" alt="" /><span>The Bits Today<span>.</span></span></>}
    {article.mediaKind === "video" && <span className="edition-play" aria-label="Video">▶</span>}
  </div>;
}

function Category({ article }: { article: Article }) {
  return <p className="edition-category"><span aria-hidden="true" />{article.workflowType === "reel" ? "Reel" : article.sourceName}</p>;
}

export function EditionCard({ article, onOpen, variant = "tile" }: {
  article: Article; onOpen: OpenStory; variant?: "tile" | "side" | "feature" | "video";
}) {
  return <article className={`edition-card edition-card--${variant}`}>
    <StoryLink article={article} onOpen={onOpen} className="edition-card-art"><Artwork article={article} /></StoryLink>
    <div className="edition-card-copy">
      <Category article={article} />
      <h3><StoryLink article={article} onOpen={onOpen}>{article.title}</StoryLink></h3>
      {variant !== "video" && <p className="edition-summary">{article.summary}</p>}
      <DateLine article={article} />
    </div>
  </article>;
}

function SectionHeading({ title, note, section, onSectionChange }: {
  title: string; note?: string; section?: Section; onSectionChange?: (section: Section) => void;
}) {
  return <div className="edition-heading">
    <h2><span aria-hidden="true" />{title}</h2>
    {note && <span className="edition-heading-note">{note}</span>}
    {section && <a href={sectionHref(section)} onClick={(event) => {
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      onSectionChange?.(section);
    }}>View all <span aria-hidden="true">→</span></a>}
  </div>;
}

function EmptySection({ children }: { children: ReactNode }) {
  return <p className="edition-empty">{children}</p>;
}

export function Edition({ articles, edition, onOpen, onSectionChange }: {
  articles: Article[];
  edition: ReturnType<typeof useHomeEdition>;
  onOpen: OpenStory;
  onSectionChange: (section: Section) => void;
}) {
  const news = edition.news.length ? edition.news : articles.filter((article) => article.workflowType === "news" || article.workflowType === "reel");
  const weekStart = edition.asOf - 7 * 24 * 60 * 60 * 1000;
  const thisWeek = (edition.weekNews.length ? edition.weekNews : news).filter((article) => article.publishedAt && Date.parse(article.publishedAt) >= weekStart && Date.parse(article.publishedAt) <= edition.asOf);
  const topStories = (edition.trending.length ? edition.trending : thisWeek.length ? thisWeek : news.length ? news : articles).slice(0, 4);
  const lead = topStories[0];
  const topIds = new Set(topStories.map((article) => article.id));
  const latest = thisWeek.filter((article) => !topIds.has(article.id)).slice(0, 4);
  const model = edition.model ?? articles.find((article) => article.workflowType === "model") ?? null;
  const thought = edition.thought ?? articles.find((article) => article.workflowType === "informative") ?? null;
  const product = edition.product ?? articles.find((article) => article.workflowType === "product") ?? null;
  const reels = edition.reels.length ? edition.reels : articles.filter((article) => article.workflowType === "reel").slice(0, 3);
  const featuredIds = new Set([...topStories, ...latest, ...reels, model, thought, product].filter((article) => article !== null).map((article) => article.id));
  const other = articles.filter((article) => !featuredIds.has(article.id));
  return <div className="edition">
    <section aria-label="Trending this week">
      <SectionHeading title="Trending this week" note={edition.trending.length ? "Instagram engagement · past 7 days" : "Latest stories until rankings are available"} />
      {lead && <div className={`edition-top ${topStories.length === 1 ? "edition-top--single" : ""}`}>
        <article className="edition-lead">
          <div className="edition-lead-copy">
            <Category article={lead} />
            <h1><StoryLink article={lead} onOpen={onOpen}>{lead.title}</StoryLink></h1>
            <p className="edition-lead-summary">{lead.description.split(/\n\s*\n/)[0]}</p>
            <DateLine article={lead} />
            <StoryLink article={lead} onOpen={onOpen} className="edition-read">View full story <span aria-hidden="true">⟶</span></StoryLink>
          </div>
          <StoryLink article={lead} onOpen={onOpen} className="edition-lead-art"><Artwork article={lead} priority /></StoryLink>
        </article>
        {topStories.length > 1 && <div className="edition-top-side">
          {topStories.slice(1).map((article) => <EditionCard key={article.id} article={article} onOpen={onOpen} variant="side" />)}
        </div>}
      </div>}
    </section>

    <section aria-label="This week’s news">
      <SectionHeading title="This week’s news" note="The past 7 days" />
      {latest.length ? <div className="edition-tiles">{latest.map((article) => <EditionCard key={article.id} article={article} onOpen={onOpen} />)}</div>
        : <EmptySection>{thisWeek.length ? "You’re caught up. This week’s stories are featured above." : "No news published in the past seven days. Explore the latest stories above."}</EmptySection>}
    </section>

    <div className="edition-middle">
      <div className="edition-middle-main">
        <section aria-label="All-time trending">
          <SectionHeading title="All-time trending" note={edition.popular.length ? "Instagram engagement" : "Latest stories until rankings are available"} />
          {(edition.popular.length || news.length) ? <ol className="edition-ranked">
            {(edition.popular.length ? edition.popular : news).slice(0, 6).map((article, index) => <li key={article.id}>
              <span className="edition-rank" aria-hidden="true">{index + 1}</span>
              <StoryLink article={article} onOpen={onOpen}>{article.title}</StoryLink>
            </li>)}
          </ol> : <EmptySection>Published news will appear here.</EmptySection>}
        </section>
        <section aria-label="Product spotlight">
          <SectionHeading title="Product spotlight" section="products" onSectionChange={onSectionChange} />
          {product ? <EditionCard article={product} onOpen={onOpen} variant="feature" /> : <EmptySection>New tools. New possibilities. Product releases will appear here as they’re published.</EmptySection>}
        </section>
      </div>
      <div className="edition-middle-side">
        <section aria-label="Model releases">
          <SectionHeading title="Model releases" section="models" onSectionChange={onSectionChange} />
          {model ? <EditionCard article={model} onOpen={onOpen} variant="feature" /> : <EmptySection>The next model release, in focus. New posts will appear here after publication.</EmptySection>}
        </section>
        <section aria-label="Tokens for Thought">
          <SectionHeading title="Tokens for Thought" section="thoughts" onSectionChange={onSectionChange} />
          {thought ? <EditionCard article={thought} onOpen={onOpen} variant="feature" /> : <EmptySection>Ideas about AI and humanity will appear here.</EmptySection>}
        </section>
      </div>
    </div>

    <section aria-label="Other stories">
      <SectionHeading title="Other stories" />
      {other.length ? <div className="edition-tiles">{other.map((article) => <EditionCard key={article.id} article={article} onOpen={onOpen} />)}</div>
        : <EmptySection>You’re up to date with this edition. More stories will appear as they’re published.</EmptySection>}
    </section>
    <section aria-label="Reels">
      <SectionHeading title="Reels" note="Watch the story" />
      {reels.length ? <div className="edition-reels">{reels.map((article) => <EditionCard key={article.id} article={article} onOpen={onOpen} variant="video" />)}</div>
        : <EmptySection>Video stories will appear here after publication.</EmptySection>}
    </section>
  </div>;
}
