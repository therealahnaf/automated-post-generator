/** A typed view of a published content-API post. */
export interface Article {
  id: string;
  workflowType: "news" | "model" | "product" | "informative" | "reel";
  title: string;
  description: string;
  summary: string;
  sourceName: string;
  sourceUrl: string;
  sources: { label: string; url: string | null }[];
  publicationUrl?: string | null;
  image: string | null;
  thumbnailImage?: string | null;
  templateBackground?: string | null;
  imageAlt: string;
  imageCaption: string;
  imageTreatment?: "photo" | "graphic" | "published";
  mediaKind?: "image" | "video";
  media?: { kind: "image" | "video"; url: string; poster_url?: string | null; mime_type: string }[];
  sourceMedia?: { kind: "image"; url: string; mime_type: string }[];
  publishedAt?: string;
  isDemo?: boolean;
  readingMinutes: number;
}
