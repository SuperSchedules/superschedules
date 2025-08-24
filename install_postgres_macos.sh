#!/bin/bash

# Install and setup PostgreSQL on macOS for Superschedules
# This script sets up PostgreSQL with the user and database from your Terraform config

set -e

echo "🚀 Setting up PostgreSQL on macOS for Superschedules"
echo "=================================================="

# Check if Homebrew is installed
if ! command -v brew &> /dev/null; then
    echo "❌ Homebrew is not installed. Please install it first:"
    echo "   /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
    exit 1
fi

# Install PostgreSQL if not already installed
if ! command -v postgres &> /dev/null; then
    echo "📦 Installing PostgreSQL via Homebrew..."
    brew install postgresql@15
    brew install pgvector
else
    echo "✅ PostgreSQL is already installed"
fi

# Start PostgreSQL service
echo "🚀 Starting PostgreSQL service..."
brew services start postgresql@15

# Wait a moment for the service to start
sleep 3

# Create the superschedules user (if it doesn't exist)
echo "👤 Creating superschedules user..."
createuser -s superschedules 2>/dev/null || echo "   User superschedules already exists"

# Create the superschedules database (if it doesn't exist)
echo "🗄️  Creating superschedules database..."
createdb -U superschedules superschedules 2>/dev/null || echo "   Database superschedules already exists"

# Enable pgvector extension
echo "🧠 Enabling pgvector extension..."
psql -U superschedules -d superschedules -c "CREATE EXTENSION IF NOT EXISTS vector;" 2>/dev/null || echo "   pgvector extension already exists"

# Test the connection
echo "🧪 Testing database connection..."
if psql -U superschedules -d superschedules -c "SELECT version();" > /dev/null 2>&1; then
    echo "✅ Database connection successful!"
else
    echo "❌ Database connection failed"
    exit 1
fi

echo ""
echo "🎉 PostgreSQL setup complete!"
echo ""
echo "Database details:"
echo "  • Database: superschedules"
echo "  • User: superschedules" 
echo "  • Host: localhost (Unix socket)"
echo "  • Authentication: peer (no password needed)"
echo ""
echo "Next steps:"
echo "1. Run Django migrations: source schedules_dev/bin/activate && python manage.py migrate"
echo "2. Update embeddings: python manage.py update_embeddings --force"
echo "3. Test the system: python setup_postgres.py"