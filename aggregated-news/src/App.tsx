import { useEffect, useState } from "react";
import { PAGE_SIZE, useArticles } from "./useArticles";
import { ArticleCard, FeaturedArticle } from "./components/ArticleCard";
import { ArticleDetail } from "./components/ArticleDetail";
import { Edition } from "./components/Edition";
import { Masthead } from "./components/Masthead";
import { SectionIntro } from "./components/SectionIntro";
import { belongsToSection, readPage, readSection, sections, type Section } from "./sections";
import { useHomeEdition } from "./useHomeEdition";

function readPostId(): string | null {
  return new URLSearchParams(window.location.search).get("post");
}

export function App() {
  const [section, setSection] = useState<Section>(readSection);
  const [page, setPage] = useState(readPage);
  const [postId, setPostId] = useState(readPostId);
  const { articles, total, status } = useArticles(section, page);
  const edition = useHomeEdition(section === "home" && page === 1 && !postId);
  useEffect(() => {
    const onLocationChange = () => {
      setSection(readSection());
      setPage(readPage());
      setPostId(readPostId());
    };
    window.addEventListener("popstate", onLocationChange);
    return () => window.removeEventListener("popstate", onLocationChange);
  }, []);
  useEffect(() => {
    const label = sections.find((item) => item.id === section)?.label;
    document.title = postId
      ? "Story | The Bits Today"
      : section === "home" ? "The Bits Today" : `${label} | The Bits Today`;
  }, [postId, section]);

  function changeSection(next: Section) {
    if (next === section && !postId) return;
    const url = new URL(window.location.href);
    if (next === "home") url.searchParams.delete("section");
    else url.searchParams.set("section", next);
    url.searchParams.delete("page");
    url.searchParams.delete("post");
    window.history.pushState(null, "", url);
    setSection(next);
    setPage(1);
    setPostId(null);
    window.scrollTo({ top: 0, behavior: "auto" });
  }

  function changePage(next: number) {
    if (next === page) return;
    const url = new URL(window.location.href);
    if (next === 1) url.searchParams.delete("page");
    else url.searchParams.set("page", String(next));
    window.history.pushState(null, "", url);
    setPage(next);
    window.scrollTo({ top: 0, behavior: "auto" });
  }

  function openPost(next: string) {
    const url = new URL(window.location.href);
    url.searchParams.set("post", next);
    window.history.pushState(null, "", url);
    setPostId(next);
    window.scrollTo({ top: 0, behavior: "auto" });
  }

  function closePost() {
    const url = new URL(window.location.href);
    url.searchParams.delete("post");
    window.history.pushState(null, "", url);
    setPostId(null);
    window.scrollTo({ top: 0, behavior: "auto" });
  }

  const visibleArticles = articles.filter((article) => belongsToSection(article, section));
  const pageCount = Math.ceil(total / PAGE_SIZE);
  const [leadArticle, ...allRemaining] = visibleArticles;
  const remaining = allRemaining;
  const sideArticles = remaining.slice(0, 3);
  const moreArticles = remaining.slice(3, 5);
  const sectionInfo = sections.find((item) => item.id === section)!;
  const returnUrl = new URL(window.location.href);
  returnUrl.searchParams.delete("post");
  return (
    <>
      <a className="skip-link" href="#main-content">
        Skip to stories
      </a>
      <div className="page-decoration" aria-hidden="true">
        <span className="corner-orbit" />
        <span className="corner-disc" />
        <span className="corner-dots" />
        <span className="corner-stripes" />
        <span className="edge-diamond" />
        <span className="edge-disc" />
      </div>
      <div className={`newspaper ${postId ? "" : "newspaper--listing"} ${!postId && section === "home" ? "newspaper--edition" : ""}`}>
        <Masthead activeSection={section} onSectionChange={changeSection} />
        <main id="main-content" tabIndex={-1}>
          {postId ? (
            <ArticleDetail
              postId={postId}
              backHref={`${returnUrl.pathname}${returnUrl.search}`}
              backLabel={sectionInfo.label}
              onBack={closePost}
            />
          ) : (
          <>
          {section !== "home" && <SectionIntro key={section} section={section} label={sectionInfo.label} description={sectionInfo.description} />}
          {status === "error" && (
            <p className="content-status panel" role="alert">
              Stories are temporarily unavailable. We’ll reconnect
              automatically.
            </p>
          )}
          {status === "loading" && (
            <p className="content-status panel" role="status">
              Loading the latest edition…
            </p>
          )}
          {status === "ready" && !leadArticle && (
            <section className="content-status panel">
              <h2>No stories in this section yet.</h2>
              <p>New posts will appear here after publication.</p>
            </section>
          )}
          {status === "ready" && leadArticle && section === "home" && page === 1 && (
            <Edition articles={articles} edition={edition} onOpen={openPost} onSectionChange={changeSection} />
          )}
          {status === "ready" && leadArticle && !(section === "home" && page === 1) && (
            <>
              <div
                className={`front-page-grid ${sideArticles.length ? "" : "front-page-grid--single"}`}
              >
                <FeaturedArticle article={leadArticle} onOpen={openPost} />
                {sideArticles.length > 0 && (
                  <section
                    className="other-news panel"
                    id="latest"
                    aria-labelledby="other-news-title"
                  >
                    <div className="section-heading">
                      <h2 className="section-label" id="other-news-title">
                        {section === "home" ? "In other news" : `More ${sectionInfo.label.toLowerCase()}`}
                      </h2>
                      <span className="three-dots" aria-hidden="true">
                        <i />
                        <i />
                        <i />
                      </span>
                    </div>
                    {sideArticles.map((article) => (
                      <ArticleCard key={article.id} article={article} onOpen={openPost} />
                    ))}
                  </section>
                )}
              </div>
              {moreArticles.length > 0 && <div className="lower-grid lower-grid--stories">
                {moreArticles.map((article) => (
                  <div className="panel bottom-story" key={article.id}>
                    <ArticleCard article={article} variant="bottom" onOpen={openPost} />
                  </div>
                ))}
              </div>}
              {remaining.length > 5 && (
                <section className="archive-grid" aria-label="More stories">
                  {remaining.slice(5).map((article) => (
                    <div className="panel" key={article.id}>
                      <ArticleCard article={article} variant="bottom" onOpen={openPost} />
                    </div>
                  ))}
                </section>
              )}
            </>
          )}
          {status === "ready" && pageCount > 1 && (
            <nav className="pagination" aria-label="Story pages">
              <button type="button" disabled={page === 1} onClick={() => changePage(page - 1)}>
                Previous
              </button>
              <span>Page {page} of {pageCount}</span>
              <button type="button" disabled={page >= pageCount} onClick={() => changePage(page + 1)}>
                Next
              </button>
            </nav>
          )}
          </>
          )}
        </main>
      </div>
    </>
  );
}
