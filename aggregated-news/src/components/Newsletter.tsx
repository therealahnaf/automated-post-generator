import { useState, type FormEvent } from "react";

export function Newsletter() {
  const [email, setEmail] = useState("");
  const [website, setWebsite] = useState("");
  const [status, setStatus] = useState<"idle" | "sending" | "success" | "error">("idle");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (status === "sending") return;
    setStatus("sending");
    try {
      const response = await fetch("/api/newsletter/subscriptions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, website }),
      });
      if (!response.ok) throw new Error("Signup unavailable");
      setStatus("success");
      setEmail("");
    } catch {
      setStatus("error");
    }
  }

  return <aside className="edition-newsletter" aria-labelledby="newsletter-title">
    <div className="edition-heading"><h2 id="newsletter-title"><span aria-hidden="true" />Newsletter</h2></div>
    <div className="edition-newsletter-content">
      <div className="edition-newsletter-intro">
        <span className="newsletter-envelope" aria-hidden="true"><svg viewBox="0 0 32 32" focusable="false"><rect x="4" y="8" width="24" height="17" rx="1" /><path d="m5 10 11 8 11-8" /></svg></span>
        <div><h3>The Bits Today, in your inbox.</h3><p>Thoughtful coverage of AI and the technology shaping what comes next.</p></div>
      </div>
      <form onSubmit={submit}>
        <label className="sr-only" htmlFor="newsletter-email">Email address</label>
        <input id="newsletter-email" type="email" autoComplete="email" placeholder="Your email address" value={email} onChange={(event) => setEmail(event.target.value)} required maxLength={254} disabled={status === "sending"} />
        <div className="newsletter-honeypot" aria-hidden="true"><label htmlFor="newsletter-website">Website</label><input id="newsletter-website" type="text" tabIndex={-1} autoComplete="off" value={website} onChange={(event) => setWebsite(event.target.value)} /></div>
        <button type="submit" disabled={status === "sending"}>{status === "sending" ? "Joining…" : "Join the list"}<span aria-hidden="true">→</span></button>
      </form>
      <p className="edition-newsletter-status" role="status">{status === "success" ? "You're on the list. The newsletter hasn't launched yet." : status === "error" ? "Couldn’t save your email. Please try again." : "Sign up now; no emails are being sent yet."}</p>
      <p className="edition-newsletter-privacy">Your email is stored for this list only. <a href="https://github.com/therealahnaf/automated-post-generator/blob/main/PRIVACY_POLICY.md" target="_blank" rel="noopener noreferrer">Privacy policy</a></p>
    </div>
  </aside>;
}
