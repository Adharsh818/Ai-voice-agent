-- Messages to patients (notify.py, 11 Oct): a confirmation when an appointment is booked, moved or
-- cancelled, and a reminder before it. Sent by WhatsApp or SMS once a provider is set up; until then the
-- log-only sender records what would have been sent. Only a new table: nothing else changes.
CREATE TABLE patient_messages (
    id              INTEGER PRIMARY KEY,
    appointment_id  TEXT NOT NULL REFERENCES appointments(id),
    kind            TEXT NOT NULL CHECK (kind IN ('booked', 'moved', 'cancelled', 'reminder')),
    channel         TEXT NOT NULL CHECK (channel IN ('whatsapp', 'sms')),
    phone_e164      TEXT NOT NULL,
    body            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'sent', 'logged', 'failed', 'skipped')),
    attempts        INTEGER NOT NULL DEFAULT 0,
    due_at          TEXT NOT NULL,
    sent_at         TEXT,
    provider_id     TEXT,
    last_error      TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX patient_messages_due ON patient_messages (status, due_at);
CREATE INDEX patient_messages_appointment ON patient_messages (appointment_id);
