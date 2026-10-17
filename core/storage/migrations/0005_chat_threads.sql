-- Conversation threads of the webapp. A thread is an id and a title set by
-- code from the first owner message; messages of a thread point to it by `thread_id`. Messages
-- written before this migration keep `thread_id` NULL and form the implicit default thread.
--
-- Same protection as every other tenant table (0001 to 0004): row-level security is enabled and
-- the policy admits only the owner named in `app.owner`; `anon`, `authenticated` and every other
-- API role get no grant at all. The application role `aw_app` is granted the tables when it exists.

CREATE TABLE chat_threads (
  owner      text        NOT NULL CHECK (owner ~ '^0x[0-9a-f]{40}$'),
  id         text        NOT NULL,
  title      text        NOT NULL CHECK (char_length(title) <= 40),
  created_at timestamptz NOT NULL,
  PRIMARY KEY (owner, id)
);
CREATE INDEX chat_threads_owner_created ON chat_threads (owner, created_at DESC);

ALTER TABLE chat_threads ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON chat_threads
  USING (owner = current_setting('app.owner', true))
  WITH CHECK (owner = current_setting('app.owner', true));

ALTER TABLE chat_messages ADD COLUMN thread_id text;
CREATE INDEX chat_messages_owner_thread_seq ON chat_messages (owner, thread_id, seq);
CREATE INDEX chat_messages_owner_job ON chat_messages (owner, job_id);

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aw_app') THEN
    GRANT SELECT, INSERT, UPDATE (title) ON chat_threads TO aw_app;
    -- Only the thread assignment of a message may change, never its text.
    GRANT UPDATE (thread_id) ON chat_messages TO aw_app;
  END IF;
END;
$$;
