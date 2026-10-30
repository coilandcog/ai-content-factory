-- 006_reference_storytelling.sql: what a reference video teaches beyond the picture (D-109):
-- its hook, narration, structure, pacing, sound, why it works and topic ideas for the channel.
ALTER TABLE reference_notes ADD COLUMN hook TEXT NOT NULL DEFAULT '';
ALTER TABLE reference_notes ADD COLUMN narration TEXT NOT NULL DEFAULT '';
ALTER TABLE reference_notes ADD COLUMN structure TEXT NOT NULL DEFAULT '';
ALTER TABLE reference_notes ADD COLUMN pacing TEXT NOT NULL DEFAULT '';
ALTER TABLE reference_notes ADD COLUMN sound TEXT NOT NULL DEFAULT '';
ALTER TABLE reference_notes ADD COLUMN why_it_works TEXT NOT NULL DEFAULT '';
ALTER TABLE reference_notes ADD COLUMN topic_ideas_json TEXT NOT NULL DEFAULT '[]';
