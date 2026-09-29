import { useEffect, useRef } from "react";

const AD_CLIENT = "ca-pub-8059416875418410";
const AD_SLOT = "8468894754";

type AdWindow = Window & {
  adsbygoogle?: Record<string, never>[];
};

export function AdSlot({ className = "" }: { className?: string }) {
  const unit = useRef<HTMLModElement>(null);
  const initialized = useRef(false);

  useEffect(() => {
    if (!unit.current || initialized.current) return;
    initialized.current = true;
    try {
      const adWindow = window as AdWindow;
      (adWindow.adsbygoogle ||= []).push({});
    } catch {
      // A blocked script or unapproved site should not break article navigation.
    }
  }, []);

  return <aside className={`ad-card ${className}`.trim()} aria-label="Advertisement">
    <span className="ad-card-label">Advertisement</span>
    <ins
      ref={unit}
      className="adsbygoogle"
      style={{ display: "block" }}
      data-ad-client={AD_CLIENT}
      data-ad-slot={AD_SLOT}
      data-ad-format="auto"
      data-full-width-responsive="true"
    />
  </aside>;
}
