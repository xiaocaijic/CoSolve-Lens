CREATE TABLE studychat_showcase_settings (
    id INTEGER PRIMARY KEY CHECK (id=1),
    replay_enabled INTEGER NOT NULL CHECK(replay_enabled IN (0,1)),
    live_enabled INTEGER NOT NULL CHECK(live_enabled IN (0,1)),
    default_mode TEXT NOT NULL CHECK(default_mode IN ('replay','live')),
    CHECK((default_mode='replay' AND replay_enabled=1) OR (default_mode='live' AND live_enabled=1))
);

INSERT INTO studychat_showcase_settings VALUES (1,1,1,'replay');
