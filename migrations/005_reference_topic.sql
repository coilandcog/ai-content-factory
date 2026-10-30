-- 005_reference_topic.sql: a reference can belong to one content item (D-104).
-- NULL = the channel's style in general (every topic); a number = only that item's prompts.
ALTER TABLE reference_notes ADD COLUMN content_id INTEGER REFERENCES contents (id);
