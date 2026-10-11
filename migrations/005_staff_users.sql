-- Named staff logins (docs/REAL_CLINIC_GAPS.md S2, 11 Oct): the audit log says who, not "staff".
-- The shared DASHBOARD_PASSWORD_HASH stays as the owner's admin login. Only a new table.
CREATE TABLE staff_users (
    id             INTEGER PRIMARY KEY,
    username       TEXT NOT NULL UNIQUE,
    display_name   TEXT NOT NULL,
    role           TEXT NOT NULL DEFAULT 'staff' CHECK (role IN ('admin', 'staff')),
    password_hash  TEXT NOT NULL,
    active         INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT NOT NULL,
    last_login_at  TEXT
);
