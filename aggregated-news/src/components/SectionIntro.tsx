import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { Section } from "../sections";

export function SectionIntro({
  section,
  label,
  description,
}: {
  section: Section;
  label: string;
  description: string;
}) {
  const panelRef = useRef<HTMLElement>(null);
  const [tabGeometry, setTabGeometry] = useState({ left: "0px", width: "72px", center: "36px" });
  const [visibleText, setVisibleText] = useState(() =>
    window.matchMedia("(prefers-reduced-motion: reduce)").matches ? description : "",
  );

  useLayoutEffect(() => {
    const updateGeometry = () => {
      const tab = document.querySelector('.news-tabs [aria-current="page"]');
      const panel = panelRef.current;
      if (!tab || !panel) return;
      const tabBounds = tab.getBoundingClientRect();
      const panelBounds = panel.getBoundingClientRect();
      const center = tabBounds.left + tabBounds.width / 2 - panelBounds.left;
      setTabGeometry({
        left: `${Math.max(0, tabBounds.left - panelBounds.left)}px`,
        width: `${tabBounds.width}px`,
        center: `${Math.max(1, Math.min(center, panelBounds.width - 1))}px`,
      });
    };
    updateGeometry();
    window.addEventListener("resize", updateGeometry);
    return () => window.removeEventListener("resize", updateGeometry);
  }, [section]);

  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    let count = 0;
    let interval = 0;
    const timeout = window.setTimeout(() => {
      interval = window.setInterval(() => {
        count = Math.min(count + 4, description.length);
        setVisibleText(description.slice(0, count));
        if (count === description.length) window.clearInterval(interval);
      }, 16);
    }, 950);
    return () => {
      window.clearTimeout(timeout);
      window.clearInterval(interval);
    };
  }, [description]);

  return (
    <section
      ref={panelRef}
      className="section-intro"
      aria-label={`About ${label}`}
      style={{
        "--tab-left": tabGeometry.left,
        "--tab-width": tabGeometry.width,
        "--tab-center": tabGeometry.center,
      } as React.CSSProperties}
    >
      <div className="section-intro-surface">
        <p aria-label={description}>
          <span className="section-intro-measure" aria-hidden="true">{description}</span>
          <span className="section-intro-typed" aria-hidden="true">
            {visibleText}
            {visibleText.length < description.length && <span className="section-intro-cursor" />}
          </span>
        </p>
      </div>
    </section>
  );
}
