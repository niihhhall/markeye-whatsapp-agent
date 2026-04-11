#!/bin/bash
# Deploy Markeye Mark AI SDR to DigitalOcean
# Prerequisites: Docker and Docker Compose installed on droplet

set -e

echo "=== Markeye Mark AI SDR Deployment ==="

# Pull latest code
git pull origin main

# Build and restart containers
docker-compose down
docker-compose build --no-cache
docker-compose up -d

# Wait for health check
echo "Waiting for services to start..."
sleep 10

# Check health
curl -f http://localhost:8000/health && echo " FastAPI is healthy" || echo " FastAPI failed"

# Show status
docker-compose ps

echo "=== Deployment complete ==="
