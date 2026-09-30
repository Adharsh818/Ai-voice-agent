-- Emma schema v1. Times ending in _utc are ISO-8601 UTC ("2026-10-05T11:30:00Z");
-- dates and HH:MM times without a suffix are clinic-local (Asia/Kolkata).

CREATE TABLE branches (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL UNIQUE,
    area         TEXT NOT NULL,
    phone        TEXT,
    calendar_id  TEXT,
    is_demo      INTEGER NOT NULL DEFAULT 0,
    active       INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE doctors (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    spoken_name  TEXT NOT NULL,
    gender       TEXT CHECK (gender IN ('female', 'male', 'other')),
    branch_id    INTEGER NOT NULL REFERENCES branches(id),
    is_demo      INTEGER NOT NULL DEFAULT 0,
    active       INTEGER NOT NULL DEFAULT 1,
    UNIQUE (name, branch_id)
);

CREATE TABLE services (
    id               INTEGER PRIMARY KEY,
    name             TEXT NOT NULL UNIQUE,
    duration_min     INTEGER NOT NULL CHECK (duration_min > 0),
    is_consultation  INTEGER NOT NULL DEFAULT 0,
    aliases_json     TEXT NOT NULL DEFAULT '[]',
    active           INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE doctor_services (
    doctor_id   INTEGER NOT NULL REFERENCES doctors(id),
    service_id  INTEGER NOT NULL REFERENCES services(id),
    PRIMARY KEY (doctor_id, service_id)
);

-- A doctor may have several rules per weekday (split shifts).
CREATE TABLE availability_rules (
    id          INTEGER PRIMARY KEY,
    doctor_id   INTEGER NOT NULL REFERENCES doctors(id),
    weekday     INTEGER NOT NULL CHECK (weekday BETWEEN 0 AND 6),  -- Monday = 0
    start_time  TEXT NOT NULL,                                     -- 'HH:MM'
    end_time    TEXT NOT NULL,
    CHECK (start_time < end_time)
);
CREATE INDEX availability_rules_doctor ON availability_rules (doctor_id, weekday);

-- Whole-day closures; branch_id NULL closes every branch.
CREATE TABLE closures (
    id         INTEGER PRIMARY KEY,
    date       TEXT NOT NULL,
    branch_id  INTEGER REFERENCES branches(id),
    reason     TEXT NOT NULL,
    is_demo    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX closures_date ON closures (date);

CREATE TABLE blocked_times (
    id               INTEGER PRIMARY KEY,
    doctor_id        INTEGER NOT NULL REFERENCES doctors(id),
    start_utc        TEXT NOT NULL,
    end_utc          TEXT NOT NULL,
    reason_category  TEXT NOT NULL
                     CHECK (reason_category IN ('illness', 'emergency', 'training', 'personal', 'other')),
    note             TEXT,
    created_by       TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    lifted_at        TEXT,
    CHECK (start_utc < end_utc)
);
CREATE INDEX blocked_times_doctor ON blocked_times (doctor_id, start_utc);

CREATE TABLE patients (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    name_norm   TEXT NOT NULL,
    phone_e164  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    UNIQUE (phone_e164, name_norm)
);

CREATE TABLE appointments (
    id                       TEXT PRIMARY KEY,
    patient_id               INTEGER NOT NULL REFERENCES patients(id),
    caller_name              TEXT,
    caller_phone_e164        TEXT NOT NULL,
    service_id               INTEGER NOT NULL REFERENCES services(id),
    doctor_id                INTEGER NOT NULL REFERENCES doctors(id),
    branch_id                INTEGER NOT NULL REFERENCES branches(id),
    start_utc                TEXT NOT NULL,
    end_utc                  TEXT NOT NULL,
    status                   TEXT NOT NULL
                             CHECK (status IN ('booked', 'cancelled', 'needs_reschedule', 'completed', 'no_show')),
    source                   TEXT NOT NULL CHECK (source IN ('inbound', 'outbound', 'dashboard', 'seed')),
    name_unverified          INTEGER NOT NULL DEFAULT 0,
    patient_age              INTEGER,
    cancel_reason            TEXT,
    affected_by_block_id     INTEGER REFERENCES blocked_times(id),
    version                  INTEGER NOT NULL DEFAULT 1,
    calendar_event_id        TEXT,
    calendar_synced_version  INTEGER,
    created_by_call_id       TEXT,
    created_at               TEXT NOT NULL,
    updated_at               TEXT NOT NULL,
    CHECK (start_utc < end_utc)
);
CREATE INDEX appointments_phone ON appointments (caller_phone_e164, status, start_utc);
CREATE INDEX appointments_doctor ON appointments (doctor_id, start_utc);

-- Offered slots are held for a short time so nobody else takes them mid-offer.
CREATE TABLE slot_holds (
    id          TEXT PRIMARY KEY,
    doctor_id   INTEGER NOT NULL REFERENCES doctors(id),
    service_id  INTEGER NOT NULL REFERENCES services(id),
    start_utc   TEXT NOT NULL,
    end_utc     TEXT NOT NULL,
    call_id     TEXT NOT NULL,
    expires_at  TEXT NOT NULL
);
CREATE INDEX slot_holds_call ON slot_holds (call_id);

-- One row per 30-minute cell a booking or hold occupies. The primary key is
-- what makes a double booking impossible, whatever the application does.
CREATE TABLE slot_claims (
    doctor_id       INTEGER NOT NULL REFERENCES doctors(id),
    cell_start_utc  TEXT NOT NULL,
    appointment_id  TEXT REFERENCES appointments(id) ON DELETE CASCADE,
    hold_id         TEXT REFERENCES slot_holds(id) ON DELETE CASCADE,
    PRIMARY KEY (doctor_id, cell_start_utc),
    CHECK ((appointment_id IS NULL) <> (hold_id IS NULL))
);
CREATE INDEX slot_claims_appointment ON slot_claims (appointment_id);
CREATE INDEX slot_claims_hold ON slot_claims (hold_id);

-- Completed actions by idempotency key: a retried request returns the stored result.
CREATE TABLE actions (
    idempotency_key  TEXT PRIMARY KEY,
    action           TEXT NOT NULL,
    result_json      TEXT NOT NULL,
    created_at       TEXT NOT NULL
);

CREATE TABLE calls (
    id                 TEXT PRIMARY KEY,
    direction          TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    started_at         TEXT NOT NULL,
    ended_at           TEXT,
    caller_phone_e164  TEXT,
    outcome            TEXT,
    workflow           TEXT,
    recording_consent  INTEGER NOT NULL DEFAULT 1,
    recording_path     TEXT,
    purge_after        TEXT
);

CREATE TABLE call_turns (
    call_id        TEXT NOT NULL REFERENCES calls(id),
    turn           INTEGER NOT NULL,
    role           TEXT NOT NULL CHECK (role IN ('caller', 'emma', 'operator')),
    text           TEXT,
    tier           INTEGER,
    state_before   TEXT,
    state_after    TEXT,
    entities_json  TEXT,
    latency_json   TEXT,
    ts             TEXT NOT NULL,
    PRIMARY KEY (call_id, turn, role)
);

CREATE TABLE tasks (
    id              INTEGER PRIMARY KEY,
    kind            TEXT NOT NULL CHECK (kind IN ('callback', 'emergency', 'red_flag', 'recovery_failed',
                                                  'escalation', 'language', 'abandoned')),
    priority        TEXT NOT NULL DEFAULT 'normal' CHECK (priority IN ('urgent', 'high', 'normal')),
    call_id         TEXT,
    appointment_id  TEXT REFERENCES appointments(id),
    phone_e164      TEXT,
    note            TEXT,
    status          TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'done')),
    created_at      TEXT NOT NULL,
    due_at          TEXT,
    done_by         TEXT
);
CREATE INDEX tasks_open ON tasks (status, priority, created_at);

CREATE TABLE contact_prefs (
    phone_e164   TEXT PRIMARY KEY,
    do_not_call  INTEGER NOT NULL DEFAULT 0,
    updated_at   TEXT NOT NULL
);

CREATE TABLE outbound_campaigns (
    id          INTEGER PRIMARY KEY,
    block_id    INTEGER NOT NULL REFERENCES blocked_times(id),
    status      TEXT NOT NULL CHECK (status IN ('running', 'stopped', 'completed')),
    created_by  TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE outbound_jobs (
    id                    INTEGER PRIMARY KEY,
    campaign_id           INTEGER NOT NULL REFERENCES outbound_campaigns(id),
    phone_e164            TEXT NOT NULL,
    appointment_ids_json  TEXT NOT NULL,
    snapshot_json         TEXT NOT NULL,
    status                TEXT NOT NULL
                          CHECK (status IN ('queued', 'ringing', 'in_call', 'done', 'failed', 'skipped')),
    outcome               TEXT,
    attempts              INTEGER NOT NULL DEFAULT 0,
    call_id               TEXT,
    idempotency_key       TEXT NOT NULL UNIQUE,
    updated_at            TEXT NOT NULL
);

-- "This appointment changed": the Calendar worker makes the event match the
-- appointment's current state, so one row per appointment is enough.
CREATE TABLE sync_outbox (
    appointment_id  TEXT PRIMARY KEY REFERENCES appointments(id),
    due_at          TEXT NOT NULL,
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'failed'))
);

CREATE TABLE audit_events (
    id              INTEGER PRIMARY KEY,
    ts              TEXT NOT NULL,
    actor           TEXT NOT NULL,
    action          TEXT NOT NULL,
    entity          TEXT NOT NULL,
    entity_id       TEXT NOT NULL,
    before_json     TEXT,
    after_json      TEXT,
    correlation_id  TEXT
);
CREATE INDEX audit_events_entity ON audit_events (entity, entity_id);
