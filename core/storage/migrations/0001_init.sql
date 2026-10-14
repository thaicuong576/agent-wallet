-- 0001_init: Postgres layout of the storage Protocols (design 5.2 and 11).
-- One set of tables; every owner-keyed table carries `owner` as the first column of its key.
-- Tenant tables have no foreign key to owners so a tenant can be opened before it is registered;
-- the owner column is checked against the lowercase address pattern instead.

-- Global tables -------------------------------------------------------------------------------
CREATE TABLE owners (
  address       text PRIMARY KEY CHECK (address ~ '^0x[0-9a-f]{40}$'),
  agent_address text NOT NULL,
  created_at    timestamptz NOT NULL
);

CREATE TABLE nonces (
  address    text NOT NULL,
  nonce      text NOT NULL,
  expires_at timestamptz NOT NULL,
  PRIMARY KEY (address, nonce)
);

CREATE TABLE sessions (
  jti        text PRIMARY KEY,
  address    text NOT NULL,
  expires_at timestamptz NOT NULL,
  revoked    boolean NOT NULL DEFAULT false
);

CREATE TABLE stepup_challenges (
  challenge_id text PRIMARY KEY,
  payload      jsonb NOT NULL,
  expires_at   timestamptz NOT NULL
);

CREATE TABLE settings (
  key   text PRIMARY KEY,
  value text NOT NULL
);

-- Tenant tables -------------------------------------------------------------------------------
CREATE TABLE keystores (
  owner      text PRIMARY KEY CHECK (owner ~ '^0x[0-9a-f]{40}$'),
  keystore   text NOT NULL,             -- Web3 Secret Storage JSON, already encrypted
  kdf        text NOT NULL,
  created_at timestamptz NOT NULL
);

CREATE TABLE audit_records (
  owner        text   NOT NULL CHECK (owner ~ '^0x[0-9a-f]{40}$'),
  seq          bigint NOT NULL CHECK (seq >= 0),
  order_id     text,
  outcome      text   NOT NULL,
  function     text,
  token        text,
  amount_value numeric,
  decimals     int,
  created_at   timestamptz NOT NULL,
  prev_hash    text   NOT NULL CHECK (length(prev_hash) = 64),
  hash         text   NOT NULL CHECK (length(hash) = 64),
  payload      text   NOT NULL,         -- canonical JSON text, the source of the hashed bytes
  PRIMARY KEY (owner, seq),
  UNIQUE (owner, hash)
);
CREATE INDEX audit_order ON audit_records (owner, order_id, seq DESC);
CREATE INDEX audit_exec  ON audit_records (owner, created_at) WHERE outcome = 'executed';

CREATE TABLE order_intents (
  owner      text NOT NULL CHECK (owner ~ '^0x[0-9a-f]{40}$'),
  order_id   text NOT NULL,
  body_hash  text NOT NULL,
  state      text NOT NULL CHECK (state = 'signing'),
  created_at timestamptz NOT NULL,
  PRIMARY KEY (owner, order_id)
);

CREATE TABLE policy_selection (
  owner          text   NOT NULL CHECK (owner ~ '^0x[0-9a-f]{40}$'),
  seq            bigint NOT NULL CHECK (seq >= 0),
  preset_id      text   NOT NULL,
  ips_hash       text   NOT NULL,
  signature_hash text   NOT NULL,
  created_at     timestamptz NOT NULL,
  PRIMARY KEY (owner, seq)
);

CREATE TABLE tier_state (
  owner          text PRIMARY KEY CHECK (owner ~ '^0x[0-9a-f]{40}$'),
  tier           text NOT NULL CHECK (tier IN ('T0', 'T1', 'T2')),
  emergency_stop boolean NOT NULL,
  updated_at     timestamptz NOT NULL,
  changed_by     text NOT NULL CHECK (changed_by = 'owner')
);

CREATE TABLE approval_queue (
  owner        text NOT NULL CHECK (owner ~ '^0x[0-9a-f]{40}$'),
  order_id     text NOT NULL,
  status       text NOT NULL CHECK (status IN ('pending', 'approved', 'rejected')),
  requested_at timestamptz NOT NULL,
  decided_at   timestamptz,
  decided_by   text CHECK (decided_by IS NULL OR decided_by = 'owner'),
  PRIMARY KEY (owner, order_id)
);

CREATE TABLE jobs (
  owner          text NOT NULL CHECK (owner ~ '^0x[0-9a-f]{40}$'),
  job_id         text NOT NULL,
  kind           text NOT NULL,
  status         text NOT NULL DEFAULT 'queued'
                 CHECK (status IN ('queued', 'running', 'succeeded', 'denied', 'failed',
                                   'interrupted')),
  planner_client text,
  order_ids      jsonb NOT NULL DEFAULT '[]'::jsonb,   -- ids only, never order bodies
  error_code     text,
  created_at     timestamptz NOT NULL,
  started_at     timestamptz,
  finished_at    timestamptz,
  PRIMARY KEY (owner, job_id)
);
-- At most one active job per owner.
CREATE UNIQUE INDEX jobs_one_active ON jobs (owner) WHERE status IN ('queued', 'running');

CREATE TABLE topups (
  owner      text NOT NULL CHECK (owner ~ '^0x[0-9a-f]{40}$'),
  tx_hash    text NOT NULL,
  amount_wei text NOT NULL,
  status     text NOT NULL,
  created_at timestamptz NOT NULL,
  PRIMARY KEY (owner, tx_hash)
);

-- Append-only: audit_records, order_intents and policy_selection reject UPDATE, DELETE, TRUNCATE.
CREATE FUNCTION aw_reject_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% is append-only: % is forbidden', TG_TABLE_NAME, TG_OP
    USING ERRCODE = 'raise_exception';
END;
$$;

CREATE TRIGGER audit_records_no_mutation BEFORE UPDATE OR DELETE ON audit_records
  FOR EACH ROW EXECUTE FUNCTION aw_reject_mutation();
CREATE TRIGGER audit_records_no_truncate BEFORE TRUNCATE ON audit_records
  FOR EACH STATEMENT EXECUTE FUNCTION aw_reject_mutation();
CREATE TRIGGER order_intents_no_mutation BEFORE UPDATE OR DELETE ON order_intents
  FOR EACH ROW EXECUTE FUNCTION aw_reject_mutation();
CREATE TRIGGER order_intents_no_truncate BEFORE TRUNCATE ON order_intents
  FOR EACH STATEMENT EXECUTE FUNCTION aw_reject_mutation();
CREATE TRIGGER policy_selection_no_mutation BEFORE UPDATE OR DELETE ON policy_selection
  FOR EACH ROW EXECUTE FUNCTION aw_reject_mutation();
CREATE TRIGGER policy_selection_no_truncate BEFORE TRUNCATE ON policy_selection
  FOR EACH STATEMENT EXECUTE FUNCTION aw_reject_mutation();

-- Row-level security: a statement without `app.owner` set sees and writes no tenant rows.
-- keystores also admits the rotation path, which sets app.owner to '*'.
ALTER TABLE audit_records    ENABLE ROW LEVEL SECURITY;
ALTER TABLE order_intents    ENABLE ROW LEVEL SECURITY;
ALTER TABLE policy_selection ENABLE ROW LEVEL SECURITY;
ALTER TABLE tier_state       ENABLE ROW LEVEL SECURITY;
ALTER TABLE approval_queue   ENABLE ROW LEVEL SECURITY;
ALTER TABLE jobs             ENABLE ROW LEVEL SECURITY;
ALTER TABLE topups           ENABLE ROW LEVEL SECURITY;
ALTER TABLE keystores        ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON audit_records
  USING (owner = current_setting('app.owner', true))
  WITH CHECK (owner = current_setting('app.owner', true));
CREATE POLICY tenant_isolation ON order_intents
  USING (owner = current_setting('app.owner', true))
  WITH CHECK (owner = current_setting('app.owner', true));
CREATE POLICY tenant_isolation ON policy_selection
  USING (owner = current_setting('app.owner', true))
  WITH CHECK (owner = current_setting('app.owner', true));
CREATE POLICY tenant_isolation ON tier_state
  USING (owner = current_setting('app.owner', true))
  WITH CHECK (owner = current_setting('app.owner', true));
CREATE POLICY tenant_isolation ON approval_queue
  USING (owner = current_setting('app.owner', true))
  WITH CHECK (owner = current_setting('app.owner', true));
CREATE POLICY tenant_isolation ON jobs
  USING (owner = current_setting('app.owner', true))
  WITH CHECK (owner = current_setting('app.owner', true));
CREATE POLICY tenant_isolation ON topups
  USING (owner = current_setting('app.owner', true))
  WITH CHECK (owner = current_setting('app.owner', true));
CREATE POLICY tenant_isolation ON keystores
  USING (owner = current_setting('app.owner', true) OR current_setting('app.owner', true) = '*')
  WITH CHECK (owner = current_setting('app.owner', true)
              OR current_setting('app.owner', true) = '*');

-- Application role. Created without login; grant it to the login role of DATABASE_URL.
-- Where role creation is not allowed (some hosted free tiers) the triggers above still hold.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aw_app') THEN
    BEGIN
      CREATE ROLE aw_app NOLOGIN NOBYPASSRLS;
    EXCEPTION WHEN insufficient_privilege THEN
      RAISE NOTICE 'aw_app not created (insufficient privilege); relying on triggers';
    END;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aw_app') THEN
    GRANT SELECT, INSERT ON audit_records, order_intents, policy_selection TO aw_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON
      owners, nonces, sessions, stepup_challenges, settings, keystores, tier_state,
      approval_queue, jobs, topups TO aw_app;
  END IF;
END;
$$;
