#!/bin/bash

echo "🚀 AI Auto-Posting Project - Fly.io Deployment Script"
echo "====================================================="

# Check if fly CLI is installed
if ! command -v fly &> /dev/null; then
    echo "❌ Fly CLI not found. Installing..."
    
    # Install Fly CLI based on OS
    if [[ "$OSTYPE" == "linux-gnu"* ]]; then
        curl -L https://fly.io/install.sh | sh
    elif [[ "$OSTYPE" == "darwin"* ]]; then
        curl -L https://fly.io/install.sh | sh
    elif [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "cygwin" ]]; then
        echo "Please install Fly CLI manually: https://fly.io/docs/hands-on/install-flyctl/"
        exit 1
    fi
    
    echo "✅ Fly CLI installed. Please restart your terminal and run this script again."
    exit 0
fi

# Check if user is logged in to Fly.io
if ! fly auth whoami &> /dev/null; then
    echo "🔐 Please login to Fly.io first:"
    echo "   fly auth login"
    exit 1
fi

echo "✅ Fly CLI is ready and authenticated"

# Check if app exists
if fly apps list | grep -q "ai-auto-posting"; then
    echo "✅ App 'ai-auto-posting' already exists"
else
    echo "📱 Creating new Fly.io app..."
    fly apps create ai-auto-posting --org personal
fi

# Check if PostgreSQL database exists
if fly postgres list | grep -q "ai-auto-posting-db"; then
    echo "✅ PostgreSQL database already exists"
else
    echo "🗄️ Creating PostgreSQL database..."
    fly postgres create ai-auto-posting-db --org personal --region lhr
fi

# Attach database to app
echo "🔗 Attaching database to app..."
fly postgres attach ai-auto-posting-db --app ai-auto-posting

# Create volume for persistent storage
if ! fly volumes list | grep -q "ai_auto_posting_data"; then
    echo "💾 Creating persistent volume..."
    fly volumes create ai_auto_posting_data --size 3 --region lhr
fi

# Deploy the application
echo "🚀 Deploying to Fly.io..."
fly deploy

echo ""
echo "🎉 Deployment complete!"
echo ""
echo "Your app is now live at: https://ai-auto-posting.fly.dev"
echo ""
echo "Next steps:"
echo "1. Set environment variables in Fly.io dashboard"
echo "2. Configure your database connection"
echo "3. Test your application"
echo ""
echo "Useful commands:"
echo "  fly logs -a ai-auto-posting          # View logs"
echo "  fly status -a ai-auto-posting        # Check status"
echo "  fly open -a ai-auto-posting          # Open in browser"
echo "  fly destroy ai-auto-posting           # Delete app (if needed)"
