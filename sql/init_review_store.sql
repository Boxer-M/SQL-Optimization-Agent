-- Run only in dedicated review_store as its initialization owner.
CREATE TABLE review_batches(id text PRIMARY KEY,raw bytea NOT NULL,created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE review_entries(batch text REFERENCES review_batches(id),entry integer CHECK(entry>0),PRIMARY KEY(batch,entry));
CREATE TABLE review_events(batch text,entry integer,version integer CHECK(version>0),decision text CHECK(decision IN ('accept','defer','reject')),reviewer text NOT NULL,reason text NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),PRIMARY KEY(batch,entry,version),FOREIGN KEY(batch,entry) REFERENCES review_entries(batch,entry));
GRANT CONNECT ON DATABASE review_store TO review_app;
GRANT USAGE ON SCHEMA public TO review_app;
GRANT SELECT,INSERT ON review_batches,review_events TO review_app;
-- UPDATE privilege enables SELECT FOR UPDATE; trigger prevents actual mutations.
GRANT SELECT,INSERT,UPDATE ON review_entries TO review_app;
CREATE FUNCTION deny_review_mutation() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Append-only review storage'; END $$;
CREATE TRIGGER immutable_entries BEFORE UPDATE OR DELETE ON review_entries FOR EACH ROW EXECUTE FUNCTION deny_review_mutation();
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
