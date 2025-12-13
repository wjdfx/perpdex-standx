CREATE TABLE IF NOT EXISTS profit_log (
    id SERIAL PRIMARY KEY,
    price FLOAT NOT NULL,
    position FLOAT NOT NULL,
    period_profit FLOAT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT now()
);