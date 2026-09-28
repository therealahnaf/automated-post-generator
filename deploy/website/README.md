# Production website

Independent Docker Compose stack: nginx, FastAPI, PostgreSQL 16. The database has
no host port. Public nginx permits only read requests to `/api`; publishing uses
the host-only API at `http://127.0.0.1:18501`. Never expose that port publicly.

Build `aggregated-news/dist` with `npm ci && npm run build`, then ship it alongside
`content_api` and this directory (exclude `.env`, caches and node_modules).
Create a mode-600 `.env` beside `compose.yml` using the example. Use random hex
values so the database URL needs no escaping. Run:

```sh
docker compose --env-file deploy/website/.env -f deploy/website/compose.yml up -d --build
curl --fail http://127.0.0.1:18573/api/health
```

The `traefik-shared` external network and existing TLS entrypoint route only
`thebitstoday.com` and `www.thebitstoday.com` to this stack. Existing applications
and watchers do not need restarting. Configure Cloudflare DNS to this VM, then
install a valid origin certificate before enabling Full (strict) proxying.

Set `CONTENT_API_URL=http://127.0.0.1:18501` and the matching `CONTENT_API_KEY` in
the publisher's root `.env` only after deploying compatible manifest/archive
scripts. Never copy workstation `.env` files or demo data to production.

Back up PostgreSQL metadata with pg_dump in custom format; media lives in private
Cloudflare R2, so database backups do not back up the objects. Do not use `down -v` during
updates. Retain the prior release to roll back application code; database
migrations are additive. Archive retries must only replay saved delivery
receipts, never social publication commands.

Configure `R2_ENDPOINT_URL`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, and
`R2_BUCKET` in the private deployment environment. After backing up, run
`docker compose --env-file deploy/website/.env -f deploy/website/compose.yml exec api python -m content_api.migrate_to_r2`.
This resumable migration verifies uploaded bytes before clearing database blobs.
Media endpoints redirect directly to short-lived R2 URLs. Original tweet videos
are browser-compatible MP4s capped at 180 seconds; posters also live in R2.

## Current VM deployment

Application files: `/opt/thebitstoday/app`. Production publisher:
`/root/automated-post-generator`. Credentials are generated on the VM and never
copied from the workstation. Integration backups are in
`/opt/thebitstoday/backups/integration-*`.

`thebitstoday-backup.timer` runs daily at 03:30 UTC (plus jitter), storing
mode-600 custom-format dumps in `/opt/thebitstoday/backups`. Off-server backup
storage/retention still needs to be configured. `thebitstoday-archive.timer`
retries confirmed pending receipts every five minutes. Both services can be run
manually with `systemctl start <name>.service`.

Legacy previews without English website manifests must have those sidecars
prepared before publication. The migration helper adds sidecars only where
saved English metadata is unambiguous; it never changes approved captions or
publishes anything. Follow the publisher's preflight error for other drafts.

DNS uses A `@` → `5.189.139.51` and CNAME `www` → `thebitstoday.com`, proxied by
Cloudflare. A Let's Encrypt certificate covers both hostnames. Direct-origin and
public HTTPS were verified with normal certificate validation. Set Cloudflare
SSL/TLS to Full (strict); this dashboard setting is managed by the account owner.

`thebitstoday-certificate.timer` checks renewal twice daily. Certbot uses the
nginx-served HTTP challenge directory, follows the existing HTTPS redirect, and
persists its account/certificates under `deploy/website/letsencrypt`. After
renewal, `install_certificate.py` copies only this site's certificate/key into
the shared proxy's existing certificate mount and updates its dynamic TLS
configuration in place. It backs up changes and never restarts the proxy or
other applications. Keep that directory private and preserve it during updates.
