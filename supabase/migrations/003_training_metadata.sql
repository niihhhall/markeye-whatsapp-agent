-- migration: training metadata

-- Add metadata column to messages for tracking model performance
ALTER TABLE messages ADD COLUMN IF NOT EXISTS metadata JSONB DEFAULT '{}';

-- Index for filtering by model or metadata properties
CREATE INDEX IF NOT EXISTS idx_messages_metadata ON messages USING GIN (metadata);

-- Ensure conversations table has exported flag (added in Phase 5 but double checking)
-- ALTER TABLE conversations ADD COLUMN IF NOT EXISTS exported BOOLEAN DEFAULT false;
