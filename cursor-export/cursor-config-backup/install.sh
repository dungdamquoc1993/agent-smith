#!/bin/bash

# Script to install Cursor configuration
# Usage: ./install.sh

CURSOR_USER_DIR="$HOME/Library/Application Support/Cursor/User"

echo "📥 Installing Cursor configuration..."

# Create User directory if it doesn't exist
mkdir -p "$CURSOR_USER_DIR"
mkdir -p "$CURSOR_USER_DIR/snippets"

# Install settings
if [ -f "settings.json" ]; then
    echo "✅ Installing user settings..."
    cp settings.json "$CURSOR_USER_DIR/settings.json"
fi

# Install keybindings
if [ -f "keybindings.json" ]; then
    echo "✅ Installing keybindings..."
    cp keybindings.json "$CURSOR_USER_DIR/keybindings.json"
fi

# Install snippets
if [ -d "snippets" ]; then
    echo "✅ Installing snippets..."
    cp -r snippets/* "$CURSOR_USER_DIR/snippets/"
fi

# Install extensions
if [ -f "extensions.txt" ]; then
    echo "✅ Installing extensions..."
    while IFS= read -r extension; do
        if [[ ! "$extension" =~ ^#.*$ ]] && [ -n "$extension" ]; then
            echo "  Installing: $extension"
            code --install-extension "$extension" 2>/dev/null || cursor --install-extension "$extension" 2>/dev/null || echo "    ⚠️  Failed to install $extension"
        fi
    done < extensions.txt
fi

# Install workspace rules
if [ -d "workspace-rules" ]; then
    echo "✅ Workspace rules found. Copy them to your project's .cursor/rules/ directory"
fi

echo "✨ Installation complete!"
echo "⚠️  Don't forget to:"
echo "   1. Restart Cursor"
echo "   2. Copy workspace-rules to your project's .cursor/rules/ if needed"
