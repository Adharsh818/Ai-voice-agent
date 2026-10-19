-- A "demo" role (19 Oct): the login printed on the owner's resume for the hosted Emma. It can sign
-- in, look at the dashboard and talk to Emma, but every change is refused (auth.require_staff).
-- SQLite can't alter a CHECK constraint, so the table is rebuilt with the same rows.
CREATE TABLE staff_users_new (
    id             INTEGER PRIMARY KEY,
    username       TEXT NOT NULL UNIQUE,
    display_name   TEXT NOT NULL,
    role           TEXT NOT NULL DEFAULT 'staff' CHECK (role IN ('admin', 'staff', 'demo')),
    password_hash  TEXT NOT NULL,
    active         INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT NOT NULL,
    last_login_at  TEXT
);
INSERT INTO staff_users_new (id, username, display_name, role, password_hash, active, created_at, last_login_at)
    SELECT id, username, display_name, role, password_hash, active, created_at, last_login_at FROM staff_users;
DROP TABLE staff_users;
ALTER TABLE staff_users_new RENAME TO staff_users;
