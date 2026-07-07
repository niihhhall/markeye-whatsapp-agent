#!/bin/bash

# Start the Baileys WhatsApp service in the background
echo "🚀 Starting Baileys WhatsApp Service..."
cd baileys-service
node index.js &
cd ..

# Start the FastAPI service in the foreground
echo "🚀 Starting FastAPI Application..."
exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1
