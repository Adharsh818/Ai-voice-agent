-- C-full (after the Day 5 demo grade): retries, pause/resume, per-patient calling hours.
-- Only added columns, so a database from before keeps working unchanged.

-- When a queued job may be tried again (NULL = now), and how many tries it gets.
ALTER TABLE outbound_jobs ADD COLUMN next_attempt_at TEXT;
ALTER TABLE outbound_jobs ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 1;

-- A running campaign that staff paused (NULL = not paused); its jobs wait.
ALTER TABLE outbound_campaigns ADD COLUMN paused_at TEXT;

-- When this number may be called, clinic-local "HH:MM" (NULL = the clinic's window).
ALTER TABLE contact_prefs ADD COLUMN call_after TEXT;
ALTER TABLE contact_prefs ADD COLUMN call_before TEXT;
