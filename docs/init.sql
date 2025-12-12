CREATE TABLE IF NOT EXISTS monitor_account (
    id SERIAL PRIMARY KEY,
    userid VARCHAR NOT NULL,
    username VARCHAR NOT NULL,
    status INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    updated_at TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT unique_monitor_account_userid UNIQUE (userid)
);