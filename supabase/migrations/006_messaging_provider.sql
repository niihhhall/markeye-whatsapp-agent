-- migration: 006_messaging_provider
-- Adds fields to support unified message routing and per-client provider selection.

ALTER TABLE clients
ADD COLUMN IF NOT EXISTS messaging_provider TEXT DEFAULT 'whatsapp_cloud' CHECK (messaging_provider IN ('whatsapp_cloud', 'baileys')),
ADD COLUMN IF NOT EXISTS outreach_template_name TEXT DEFAULT 'markeye_outreach',
ADD COLUMN IF NOT EXISTS whatsapp_phone_number_id TEXT,
ADD COLUMN IF NOT EXISTS whatsapp_access_token TEXT;

-- Index for lookup optimization
CREATE INDEX IF NOT EXISTS idx_clients_provider ON clients(messaging_provider);

COMMENT ON COLUMN clients.messaging_provider IS 'Defines if the client uses official WhatsApp Cloud API or Baileys bridge.';
COMMENT ON COLUMN clients.outreach_template_name IS 'The Meta-approved template name to use for initial outreach when using Cloud API.';
