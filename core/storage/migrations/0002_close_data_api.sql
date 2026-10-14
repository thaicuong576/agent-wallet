-- Hosted Postgres providers (Supabase) expose the `public` schema through an HTTP data API for the
-- roles `anon` and `authenticated`. This application talks to the database only through
-- DATABASE_URL, never through that API, so close it: no API role may read or write our tables
-- (keystores, sessions, nonces, settings, audit log). Roles that do not exist are skipped.
DO $$
DECLARE
  r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['anon', 'authenticated'] LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA public FROM %I', r);
      EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM %I', r);
      EXECUTE format('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM %I', r);
      EXECUTE format(
        'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM %I', r);
    END IF;
  END LOOP;
END;
$$;
