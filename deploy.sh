#!/bin/bash

set -e

echo "🚀 Deploying MCP Server..."

# Pull latest changes
git pull origin main

# Build and deploy
docker-compose down
docker-compose build
docker-compose up -d

echo "✅ Deployment completed!"
echo "📊 Server is running on port 80/443"