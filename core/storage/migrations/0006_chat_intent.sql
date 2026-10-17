-- Chat message intent.
-- Adds nullable `intent` column to `chat_messages` (question | clarify | action | refusal).
-- Idempotent, no backfill needed.

ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS intent text;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aw_app') THEN
    GRANT UPDATE (intent) ON chat_messages TO aw_app;
  END IF;
END;
$$;
