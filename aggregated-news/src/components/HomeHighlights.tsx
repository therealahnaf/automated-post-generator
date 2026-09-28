import type { Article } from "../types";
import { sectionHref, type Section } from "../sections";
import { ArticleCard } from "./ArticleCard";

const slots: { section: "models" | "thoughts"; title: string; link: string }[] = [
  { section: "models", title: "Latest model release", link: "All models" },
  { section: "thoughts", title: "Tokens for Thought", link: "All thoughts" },
];

export function HomeHighlights({
  model,
  thought,
  onOpen,
  onSectionChange,
}: {
  model: Article | null;
  thought: Article | null;
  onOpen: (postId: string) => void;
  onSectionChange: (section: Section) => void;
}) {
  return (
    <section className="home-highlights" aria-label="Latest featured sections">
      {slots.map((slot) => {
        const article = slot.section === "models" ? model : thought;
        return (
          <div className="home-highlight" key={slot.section}>
            <div className="home-highlight-heading">
              <h2><span className="square" aria-hidden="true" />{slot.title}</h2>
              <a
                href={sectionHref(slot.section)}
                onClick={(event) => {
                  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
                  event.preventDefault();
                  onSectionChange(slot.section);
                }}
              >
                {slot.link} <span aria-hidden="true">→</span>
              </a>
            </div>
            <div className="home-highlight-card panel">
              {article ? (
                <ArticleCard article={article} variant="bottom" onOpen={onOpen} />
              ) : (
                <p>New stories will appear here after publication.</p>
              )}
            </div>
          </div>
        );
      })}
    </section>
  );
}
