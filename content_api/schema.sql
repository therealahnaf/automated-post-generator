CREATE TABLE IF NOT EXISTS content_schema_version (version integer PRIMARY KEY);
CREATE TABLE IF NOT EXISTS content_posts (
    id uuid PRIMARY KEY,
    external_key text NOT NULL UNIQUE,
    workflow_type text NOT NULL CHECK (workflow_type IN ('news','model','product','informative','reel')),
    title text NOT NULL,
    description text NOT NULL,
    sources jsonb NOT NULL,
    published_at timestamptz NOT NULL,
    publication_url text,
    is_demo boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS content_posts_date ON content_posts (published_at DESC, id DESC);
CREATE TABLE IF NOT EXISTS content_media (
    sha256 text PRIMARY KEY,
    mime_type text NOT NULL,
    data bytea NOT NULL,
    byte_size bigint NOT NULL CHECK (byte_size > 0)
);
CREATE TABLE IF NOT EXISTS content_post_media (
    post_id uuid NOT NULL REFERENCES content_posts(id) ON DELETE CASCADE,
    position integer NOT NULL CHECK(position >= 0),
    sha256 text NOT NULL REFERENCES content_media(sha256),
    PRIMARY KEY(post_id, position)
);
CREATE TABLE IF NOT EXISTS content_post_source_media (
    post_id uuid NOT NULL REFERENCES content_posts(id) ON DELETE CASCADE,
    position integer NOT NULL CHECK(position >= 0),
    sha256 text NOT NULL REFERENCES content_media(sha256),
    PRIMARY KEY(post_id, position)
);
CREATE TABLE IF NOT EXISTS content_post_assets (
    post_id uuid NOT NULL REFERENCES content_posts(id) ON DELETE CASCADE,
    position integer NOT NULL CHECK(position >= 0),
    asset_type text NOT NULL CHECK(asset_type IN ('x_photo', 'generated_background', 'bundled_background')),
    origin text,
    source_url text,
    sha256 text NOT NULL REFERENCES content_media(sha256),
    PRIMARY KEY(post_id, position)
);
INSERT INTO content_post_assets (post_id, position, asset_type, origin, sha256)
SELECT pm.post_id, pm.position, 'x_photo', 'main', pm.sha256
FROM content_post_source_media pm
WHERE true
ON CONFLICT (post_id, position) DO NOTHING;
INSERT INTO content_schema_version(version) VALUES (1) ON CONFLICT DO NOTHING;
ALTER TABLE content_media ALTER COLUMN data DROP NOT NULL;
ALTER TABLE content_media ADD COLUMN IF NOT EXISTS object_key text;
ALTER TABLE content_media ADD COLUMN IF NOT EXISTS poster_key text;
ALTER TABLE content_posts ADD COLUMN IF NOT EXISTS video_origin text;
INSERT INTO content_schema_version(version) VALUES (2) ON CONFLICT DO NOTHING;
