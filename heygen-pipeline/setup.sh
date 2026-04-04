#!/bin/bash

# HeyGen Pipeline Quick Setup Script
# Run this to quickly set up the pipeline

echo "🎬 CrowdTamers HeyGen Pipeline Setup"
echo "===================================="
echo ""

# Check Node.js
if ! command -v node &> /dev/null; then
    echo "❌ Node.js is not installed. Please install Node.js 18+ first."
    exit 1
fi

NODE_VERSION=$(node --version | cut -d'v' -f2 | cut -d'.' -f1)
if [ "$NODE_VERSION" -lt 18 ]; then
    echo "❌ Node.js version 18+ required. Found: $(node --version)"
    exit 1
fi

echo "✓ Node.js $(node --version)"

# Check for API key
API_KEY_PATH="$HOME/.config/heygen/api_key"
if [ ! -f "$API_KEY_PATH" ]; then
    echo ""
    echo "⚠️  API key not found at $API_KEY_PATH"
    echo ""
    read -p "Enter your HeyGen API key: " API_KEY
    
    mkdir -p "$HOME/.config/heygen"
    echo "$API_KEY" > "$API_KEY_PATH"
    echo "✓ API key saved"
else
    echo "✓ API key found"
fi

# Install dependencies
echo ""
echo "📦 Installing dependencies..."
npm install

# Copy config if needed
if [ ! -f "config.yaml" ]; then
    echo ""
    echo "📝 Creating config.yaml..."
    cp config.example.yaml config.yaml
    echo "✓ config.yaml created"
    echo "   Edit this file to set your default avatar and voice"
else
    echo "✓ config.yaml already exists"
fi

# Create directories
echo ""
echo "📁 Creating directories..."
mkdir -p videos metadata translations
echo "✓ Directories created"

# Test API connection
echo ""
echo "🧪 Testing API connection..."
node src/cli.js avatars > /dev/null 2>&1

if [ $? -eq 0 ]; then
    echo "✓ API connection successful"
else
    echo "⚠️  API test failed - check your API key"
fi

echo ""
echo "===================================="
echo "✅ Setup complete!"
echo ""
echo "Next steps:"
echo "  1. Edit config.yaml to set your default avatar and voice"
echo "  2. Run: node src/cli.js avatars    # to see available avatars"
echo "  3. Run: node src/cli.js voices     # to see available voices"
echo "  4. Create your first video:"
echo "     node src/cli.js create -s 'Hello world!' --wait"
echo ""
echo "📖 Documentation: README.md"
echo "📝 Setup guide: SETUP.md"
echo ""
