import type { Article } from "./types";

export type Section = "home" | "models" | "products" | "thoughts";

export const sections: {
  id: Section;
  label: string;
  description: string;
}[] = [
  { id: "home", label: "Home", description: "The Bits Today brings together the latest in AI: emerging technologies, major breakthroughs, and the ideas shaping what comes next. Follow the rapid growth of the field and what it could mean for humanity." },
  { id: "models", label: "Models", description: "New AI model releases and their benchmarks, gathered in one place. See what each model can do and how it compares." },
  { id: "products", label: "Products", description: "Discover useful AI products and open-source tools that make everyday work easier. Explore ways to build on your skills and do more with AI." },
  { id: "thoughts", label: "Tokens for Thought", description: "A space for philosophical questions about AI and humanity: how we coexist, what future generations of AI may change, and what those changes could mean for us." },
];

export function readSection(): Section {
  const requested = new URLSearchParams(window.location.search).get("section");
  return sections.find((section) => section.id === requested)?.id ?? "home";
}

export function readPage(): number {
  const requested = Number(new URLSearchParams(window.location.search).get("page"));
  return Number.isInteger(requested) && requested >= 1 && requested <= 16667
    ? requested
    : 1;
}

export function sectionHref(section: Section): string {
  return section === "home" ? window.location.pathname : `?section=${section}`;
}

export function belongsToSection(article: Article, section: Section): boolean {
  switch (section) {
    case "home":
      return true;
    case "models":
      return article.workflowType === "model";
    case "products":
      return article.workflowType === "product";
    case "thoughts":
      return article.workflowType === "informative";
  }
}
