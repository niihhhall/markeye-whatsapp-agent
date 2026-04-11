-- migration: multi-tenant architecture

CREATE TABLE IF NOT EXISTS clients (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  business_name TEXT NOT NULL,
  whatsapp_number TEXT UNIQUE NOT NULL,
  system_prompt TEXT NOT NULL,
  qualification_questions JSONB DEFAULT '[]',
  bant_criteria JSONB DEFAULT '{"budget_threshold": 5, "authority_threshold": 5, "need_threshold": 5, "timeline_threshold": 5, "overall_threshold": 25}',
  calendly_link TEXT,
  greeting_message TEXT DEFAULT 'Hey! Thanks for reaching out. I''m Mark from {business_name}. Quick question — what made you fill out the form today?',
  active BOOLEAN DEFAULT true,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- Add client_id to leads
ALTER TABLE leads ADD COLUMN IF NOT EXISTS client_id UUID REFERENCES clients(id);

-- Add client_id to messages
ALTER TABLE messages ADD COLUMN IF NOT EXISTS client_id UUID REFERENCES clients(id);

-- Conversations table for structured exports and analytics
CREATE TABLE IF NOT EXISTS conversations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id UUID REFERENCES clients(id),
  lead_id UUID REFERENCES leads(id),
  messages_jsonl TEXT,
  quality_label TEXT CHECK (quality_label IN ('good', 'bad', 'neutral')),
  exported BOOLEAN DEFAULT false,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- Indices for performance
CREATE INDEX IF NOT EXISTS idx_leads_client ON leads(client_id);
CREATE INDEX IF NOT EXISTS idx_messages_client ON messages(client_id);
CREATE INDEX IF NOT EXISTS idx_conversations_client ON conversations(client_id);
CREATE INDEX IF NOT EXISTS idx_conversations_export ON conversations(quality_label, exported);

-- Seed a default client for the pilot (using current settings)
-- Replace [YOUR_NUMBER] with real number later or leave for manual entry
-- INSERT INTO clients (business_name, whatsapp_number, system_prompt, calendly_link)
-- VALUES ('Markeye Default', 'whatsapp:+447700900000', '...', 'https://calendly.com/markeye');
