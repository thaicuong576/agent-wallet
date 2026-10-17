-- Chat messages of the webapp. Text and ids only: an agent step is rebuilt from the
-- audit log by `job_id`, so no order body, key or model transcript is stored here.
--
-- Same protection as every other tenant table (0001 to 0003): row-level security is enabled and
-- the policy admits only the owner named in `app.owner`; `anon`, `authenticated` and every other
-- API role get no grant at all. The application role `aw_app` is granted the table when it exists.

CREATE TABLE chat_messages (
  owner      text        NOT NULL CHECK (owner ~ '^0x[0-9a-f]{40}$'),
  seq        bigint      GENERATED ALWAYS AS IDENTITY,
  id         text        NOT NULL,
  role       text        NOT NULL CHECK (role IN ('user', 'agent')),
  text       text        NOT NULL CHECK (char_length(text) <= 4000),
  job_id     text,
  created_at timestamptz NOT NULL,
  PRIMARY KEY (owner, id)
);
CREATE INDEX chat_messages_owner_seq ON chat_messages (owner, seq);

ALTER TABLE chat_messages ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON chat_messages
  USING (owner = current_setting('app.owner', true))
  WITH CHECK (owner = current_setting('app.owner', true));

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aw_app') THEN
    GRANT SELECT, INSERT ON chat_messages TO aw_app;
  END IF;
END;
$$;
