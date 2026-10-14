-- Row level security on the six tables the Supabase advisor flags, plus two columns the webapp
-- services need. Existing tables were already protected from the data API roles by migration 0002
-- (no grants); this is defence in depth: with RLS on and no policy for them, `anon`,
-- `authenticated` and any other role that is not the table owner get no rows even if a grant is
-- ever added by mistake.
--
-- The application role `aw_app` (created in 0001, granted to the login role of DATABASE_URL) keeps
-- working through an explicit permissive policy, written only when that role exists. The table
-- owner (the migration role) bypasses RLS as before.

-- Columns written by the job and top-up services (ids and numbers only, never an order body).
ALTER TABLE jobs ADD COLUMN seqs jsonb NOT NULL DEFAULT '[]'::jsonb;   -- audit sequence numbers
ALTER TABLE topups ADD COLUMN block_number bigint;

ALTER TABLE schema_migrations  ENABLE ROW LEVEL SECURITY;
ALTER TABLE owners             ENABLE ROW LEVEL SECURITY;
ALTER TABLE nonces             ENABLE ROW LEVEL SECURITY;
ALTER TABLE sessions           ENABLE ROW LEVEL SECURITY;
ALTER TABLE stepup_challenges  ENABLE ROW LEVEL SECURITY;
ALTER TABLE settings           ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aw_app') THEN
    -- The app role only reads the migration ledger (it verifies the schema version, it never
    -- applies a migration).
    GRANT SELECT ON schema_migrations TO aw_app;
    CREATE POLICY app_read ON schema_migrations FOR SELECT TO aw_app USING (true);

    CREATE POLICY app_all ON owners            FOR ALL TO aw_app USING (true) WITH CHECK (true);
    CREATE POLICY app_all ON nonces            FOR ALL TO aw_app USING (true) WITH CHECK (true);
    CREATE POLICY app_all ON sessions          FOR ALL TO aw_app USING (true) WITH CHECK (true);
    CREATE POLICY app_all ON stepup_challenges FOR ALL TO aw_app USING (true) WITH CHECK (true);
    CREATE POLICY app_all ON settings          FOR ALL TO aw_app USING (true) WITH CHECK (true);
  END IF;
END;
$$;
