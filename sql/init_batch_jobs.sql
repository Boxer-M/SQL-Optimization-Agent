-- Apply once to the dedicated review_store as its owner, after init_review_store.sql.
CREATE TABLE review_jobs (
 id text PRIMARY KEY, files jsonb NOT NULL, settings jsonb NOT NULL,
 status text NOT NULL CHECK(status IN ('queued','running','completed','interrupted','failed')),
 submitted_by text NOT NULL, parent_id text REFERENCES review_jobs(id),
 batch_id text REFERENCES review_batches(id), error text,
 created_at timestamptz NOT NULL DEFAULT now(), started_at timestamptz, finished_at timestamptz
);
GRANT SELECT,INSERT,UPDATE ON review_jobs TO review_app;
