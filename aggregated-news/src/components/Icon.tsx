import type { SVGProps } from "react";

type IconName = "arrow" | "search" | "menu" | "close" | "external";

const paths: Record<IconName, string> = {
  arrow: "M3 12h18m-7-7 7 7-7 7",
  search: "m21 21-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0",
  menu: "M3 5h18M3 12h18M3 19h18",
  close: "m5 5 14 14M5 19 19 5",
  external: "M14 3h7v7M21 3 10 14M10 3H3v18h18v-7",
};

export function Icon({
  name,
  ...props
}: SVGProps<SVGSVGElement> & { name: IconName }) {
  return (
    <svg
      width="24"
      height="24"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...props}
    >
      <path d={paths[name]} />
    </svg>
  );
}
