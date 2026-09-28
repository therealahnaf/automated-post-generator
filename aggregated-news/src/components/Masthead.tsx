import { sections, sectionHref, type Section } from "../sections";

export function Masthead({
  activeSection,
  onSectionChange,
}: {
  activeSection: Section;
  onSectionChange: (section: Section) => void;
}) {
  const now = new Date();
  return (
    <header>
      <div className="masthead">
        <div className="wordmark" aria-label="The Bits Today">
          <span className="wordmark-title">
            <img
              className="brand-logo"
              src="/images/bits-today-logo.png"
              alt=""
              width="648"
              height="606"
            />
            <span>
              The Bits Today<span className="wordmark-dot">.</span>
            </span>
          </span>
        </div>
        <div className="masthead-edition">
          <span className="edition-diamond" aria-hidden="true" />
          <time dateTime={now.toISOString().slice(0, 10)}>
            <span>{now.toLocaleDateString("en-US", { weekday: "long" })},</span>
            <span>
              {now.toLocaleDateString("en-US", {
                month: "long",
                day: "numeric",
                year: "numeric",
              })}
            </span>
          </time>
        </div>
      </div>
      <div className="navigation">
        <nav className="news-tabs" aria-label="News sections">
          {sections.map((section) => (
            <a
              key={section.id}
              href={sectionHref(section.id)}
              aria-current={activeSection === section.id ? "page" : undefined}
              onClick={(event) => {
                if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
                event.preventDefault();
                onSectionChange(section.id);
              }}
            >
              {section.label}
            </a>
          ))}
        </nav>
      </div>
    </header>
  );
}
