export function Newsletter() {
  return (
    <aside className="newsletter panel" aria-labelledby="newsletter-title">
      <h2 className="section-label" id="newsletter-title">
        <span className="square" aria-hidden="true" />
        Stay informed
      </h2>
      <p>
        A little perspective,
        <br />
        delivered to your inbox.
      </p>
      <form>
        <label className="sr-only" htmlFor="newsletter-email">
          Your email address
        </label>
        <input
          id="newsletter-email"
          type="email"
          placeholder="Your email address"
          disabled
        />
        <button className="subscribe-button" type="button" disabled>
          Subscribe
        </button>
      </form>
      <p className="newsletter-note">
        Coming soon. Subscriptions are not open yet.
      </p>
    </aside>
  );
}
