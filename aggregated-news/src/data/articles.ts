import type { Article } from "../types";

// Curated fixture snapshots from existing output/. No runtime dependency on the
// Python pipelines, repository environment variables, or generated metadata.
export const leadArticle: Article = {
  id: "zai-data-center",
  workflowType: "news",
  title: "China’s Z.AI starts a 1-gigawatt AI data center",
  summary:
    "Built entirely with domestic chips. Enough power for roughly 750,000 homes.",
  description:
    "China’s Z.AI has begun operating a one-gigawatt AI data center built entirely with domestic chips, according to the source post. The reported power capacity is equivalent to the electricity needs of roughly 750,000 homes.",
  sourceName: "Polymarket",
  sourceUrl: "https://x.com/Polymarket/status/2079479742802141202",
  sources: [{ label: "Polymarket", url: "https://x.com/Polymarket/status/2079479742802141202" }],
  image: "/images/data-center.png",
  imageAlt:
    "An illustrated aerial view of a sprawling data-center campus at dusk",
  imageCaption:
    "The infrastructure behind the next wave of artificial intelligence.",
  readingMinutes: 2,
};

export const sideArticles: readonly Article[] = [
  {
    id: "gemini-flash",
    workflowType: "model",
    title: "Meet Gemini 3.6 Flash",
    summary: "Google’s next step in more capable, more efficient AI.",
    description:
      "Google has unveiled Gemini 3.6 Flash, promising higher intelligence, greater token efficiency, and a lower price. The company says the model was shaped directly by developer feedback.",
    sourceName: "Logan Kilpatrick",
    sourceUrl: "https://x.com/OfficialLoganK/status/2079590123038204255",
    sources: [{ label: "Logan Kilpatrick", url: "https://x.com/OfficialLoganK/status/2079590123038204255" }],
    image: "/images/gemini.png",
    imageAlt:
      "Abstract architectural illustration from the Gemini model announcement artwork",
    imageCaption: "Existing AI illustration for The Bits Today.",
    readingMinutes: 2,
  },
  {
    id: "unitree-as2",
    workflowType: "news",
    title: "A robot built to go the distance",
    summary: "Unitree’s AS2-W carries 16kg and travels over 30km unloaded.",
    description:
      "Unitree’s AS2-W demonstrates fluid movement while supporting a continuous 16kg payload and more than 30km of unloaded driving range, according to Unitree.",
    sourceName: "Unitree Robotics",
    sourceUrl: "https://x.com/UnitreeRobotics/status/2080549171661295907",
    sources: [{ label: "Unitree Robotics", url: "https://x.com/UnitreeRobotics/status/2080549171661295907" }],
    image: "/images/unitree.jpg",
    imageTreatment: "photo",
    imageAlt: "Unitree’s wheeled quadruped robot in a demonstration video",
    imageCaption: "Video still: Unitree Robotics.",
    readingMinutes: 1,
  },
  {
    id: "twitch-ai",
    workflowType: "news",
    title: "Twitch turns AI training on by default",
    summary: "A new setting reignites the debate over creators’ consent.",
    description:
      "Twitch has enabled by default a setting allowing channel content to train generative AI models across Amazon. Creators can disable Training for Generative AI in Security & Privacy settings.",
    sourceName: "Interesting AI",
    sourceUrl: "https://x.com/interesting_aIl/status/2089546324072878355",
    sources: [{ label: "Interesting AI", url: "https://x.com/interesting_aIl/status/2089546324072878355" }],
    image: "/images/twitch.jpg",
    imageTreatment: "photo",
    imageAlt:
      "Source image accompanying the discussion about Twitch’s AI training policy",
    imageCaption: "Image from the original source post.",
    readingMinutes: 3,
  },
];

export const moreArticles: readonly Article[] = [
  {
    id: "human-purpose",
    workflowType: "informative",
    title: "When AI makes your old purpose feel obsolete",
    summary: "What remains uniquely human when the work begins to change?",
    description:
      "AI may be able to perform work that once defined years of human effort, but that does not make people obsolete. The post explores meaning, identity, and the values people still choose.",
    sourceName: "Samuel Stanton",
    sourceUrl: "https://x.com/samuel_stanton_/status/2080474274469388342",
    sources: [{ label: "Samuel Stanton", url: "https://x.com/samuel_stanton_/status/2080474274469388342" }],
    image: "/images/thoughts.png",
    imageAlt: "Abstract mint and charcoal illustration for Tokens for Thought",
    imageCaption: "From The Bits Today background collection.",
    imageTreatment: "graphic",
    readingMinutes: 4,
  },
  {
    id: "ai-cybersecurity",
    workflowType: "news",
    title: "The changing face of cybersecurity",
    summary: "AI is reshaping ransomware. Companies are feeling the pressure.",
    description:
      "U.S. companies are reportedly facing a surge in AI-enabled cyberattacks. Research in the existing coverage examines how AI is making attacks more effective and accelerating the need for stronger defenses.",
    sourceName: "Polymarket",
    sourceUrl: "https://x.com/Polymarket/status/2081732508941517284",
    sources: [{ label: "Polymarket", url: "https://x.com/Polymarket/status/2081732508941517284" }],
    image: "/images/cybersecurity.png",
    imageAlt:
      "Editorial illustration of computers and cybersecurity infrastructure",
    imageCaption: "Existing AI illustration for The Bits Today.",
    readingMinutes: 3,
  },
];

export const articles: readonly Article[] = [
  leadArticle,
  ...sideArticles,
  ...moreArticles,
];
