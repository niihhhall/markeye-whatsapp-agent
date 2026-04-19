-- Add training_data table for Phase 3 Dashboard integration
CREATE TABLE IF NOT EXISTS training_data (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  lead_id       uuid REFERENCES leads(id) ON DELETE SET NULL,
  score         integer NOT NULL,
  outcome       text NOT NULL,
  history       jsonb NOT NULL,
  is_exported   boolean DEFAULT false,
  manual_score  integer,
  feedback      text,
  is_reviewed   boolean DEFAULT false,
  created_at    timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_training_data_score ON training_data(score DESC);
CREATE INDEX IF NOT EXISTS idx_training_data_created_at ON training_data(created_at DESC);
