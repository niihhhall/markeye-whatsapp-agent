#!/bin/bash
# First-time setup for DigitalOcean droplet
# Run this once on a fresh Ubuntu 24.04 droplet

set -e

echo "=== Setting up DigitalOcean Droplet ==="

# Update system
apt update && apt upgrade -y

# Install Docker
curl -fsSL https://get.docker.com | sh

# Install Docker Compose
apt install -y docker-compose-plugin

# Install git
apt install -y git

# Clone repo
git clone https://github.com/niihhhall/markeye-whatsapp-agent.git
cd markeye-whatsapp-agent

# Create .env from example
cp .env.example .env
echo "Edit .env with your API keys: nano .env"

echo "=== Setup complete ==="
echo "Next steps:"
echo "1. Edit .env with your API keys"
echo "2. Run: docker-compose up -d"
echo "3. Check: docker-compose logs baileys (scan QR code)"
echo "4. Verify: curl http://localhost:8000/health"
