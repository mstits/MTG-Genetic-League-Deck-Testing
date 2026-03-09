#!/bin/bash
# vibe-check.sh — Environment validation script for Turnkey Deployment v2
# Checks if the host has the required tools and ports available.

echo "Running MTG Genetic League Vibe Check..."
echo "----------------------------------------"

ERRORS=0

# 1. Check Docker
if ! command -v docker &> /dev/null; then
    echo "❌ Docker is missing. Please install Docker -> https://docs.docker.com/get-docker/"
    ERRORS=$((ERRORS+1))
else
    echo "✅ Docker is installed: $(docker --version)"
fi

# 2. Check Docker Compose
if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
    echo "❌ Docker Compose is missing. Please install Docker Compose."
    ERRORS=$((ERRORS+1))
else
    echo "✅ Docker Compose is installed."
fi

# 3. Check for available ports
check_port() {
    if lsof -i :$1 > /dev/null 2>&1; then
        echo "❌ Port $1 is already in use. MTG League needs this port."
        ERRORS=$((ERRORS+1))
    else
        echo "✅ Port $1 is free."
    fi
}

echo "Checking required network ports..."
check_port 8000  # FastAPI
check_port 5432  # PostgreSQL
check_port 6379  # Redis
check_port 19530 # Milvus VectorDB

# 4. Check for .env file or rely on docker-compose defaults
if [ ! -f ".env" ]; then
    echo "⚠️  No .env file found. Docker will use default environments."
else
    echo "✅ .env file present."
fi

# 5. Final Report
echo "----------------------------------------"
if [ $ERRORS -gt 0 ]; then
    echo "🚨 Vibe Check FAILED with $ERRORS errors. Please fix them before running docker-compose up."
    exit 1
else
    echo "✨ Vibe Check PASSED. You are cleared for launch!"
    echo "🚀 Run: docker-compose up --build -d"
    exit 0
fi
