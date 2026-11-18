\set ON_ERROR_STOP on
\set ECHO none
-- The caller supplies a SCRAM verifier, never a plaintext password or CLI argument.
\getenv reader_verifier POSTGRES_READER_PASSWORD_VERIFIER
\if :{?reader_verifier}
\else
    \echo 'Missing POSTGRES_READER_PASSWORD_VERIFIER; use the README command.'
    \quit 1
\endif

BEGIN;

DO $$
BEGIN
    IF current_database() <> 'sql_optimization' THEN
        RAISE EXCEPTION 'Connect to sql_optimization before running this script';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sql_optimization_reader') THEN
        RAISE EXCEPTION 'Role sql_optimization_reader already exists; inspect its purpose and privileges before proceeding';
    END IF;
    IF to_regclass('public.orders') IS NULL THEN
        RAISE EXCEPTION 'Load public.orders before creating the reader';
    END IF;
END;
$$;

CREATE ROLE sql_optimization_reader WITH LOGIN NOINHERIT NOSUPERUSER
    NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
    PASSWORD :'reader_verifier';
\unset reader_verifier

GRANT CONNECT ON DATABASE sql_optimization TO sql_optimization_reader;
GRANT USAGE ON SCHEMA public TO sql_optimization_reader;
GRANT SELECT ON TABLE public.orders TO sql_optimization_reader;

-- Detect permissions inherited through PUBLIC without changing other users' ACLs.
DO $$
BEGIN
    IF has_schema_privilege('sql_optimization_reader', 'public', 'CREATE')
        OR has_table_privilege('sql_optimization_reader', 'public.orders',
            'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
        OR EXISTS (
            SELECT 1 FROM pg_class
            WHERE relnamespace = 'public'::regnamespace
                AND relkind IN ('r', 'p', 'v', 'm', 'f')
                AND oid <> 'public.orders'::regclass
                AND has_table_privilege('sql_optimization_reader', oid,
                    'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
        ) THEN
        RAISE EXCEPTION 'Existing PUBLIC grants exceed the intended reader permissions; rolling back';
    END IF;
END;
$$;

COMMIT;
