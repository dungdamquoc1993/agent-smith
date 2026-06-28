#!/bin/bash

# Script to export Cursor IDE configuration
# Usage: ./export_cursor_config.sh [output_dir]

OUTPUT_DIR="${1:-./cursor-config-backup}"
CURSOR_USER_DIR="$HOME/Library/Application Support/Cursor/User"

echo "📦 Exporting Cursor configuration..."
mkdir -p "$OUTPUT_DIR"

# Export User Settings
if [ -f "$CURSOR_USER_DIR/settings.json" ]; then
    echo "✅ Copying user settings..."
    cp "$CURSOR_USER_DIR/settings.json" "$OUTPUT_DIR/settings.json"
else
    echo "⚠️  User settings not found at $CURSOR_USER_DIR/settings.json"
fi

# Export Keybindings
if [ -f "$CURSOR_USER_DIR/keybindings.json" ]; then
    echo "✅ Copying keybindings..."
    cp "$CURSOR_USER_DIR/keybindings.json" "$OUTPUT_DIR/keybindings.json"
else
    echo "⚠️  Keybindings not found"
fi

# Export Snippets
if [ -d "$CURSOR_USER_DIR/snippets" ]; then
    echo "✅ Copying snippets..."
    cp -r "$CURSOR_USER_DIR/snippets" "$OUTPUT_DIR/"
else
    echo "⚠️  Snippets directory not found"
fi

# Export Extensions list
echo "✅ Exporting extensions list..."
code --list-extensions > "$OUTPUT_DIR/extensions.txt" 2>/dev/null || cursor --list-extensions > "$OUTPUT_DIR/extensions.txt" 2>/dev/null || echo "# Install extensions manually" > "$OUTPUT_DIR/extensions.txt"

# Export workspace rules (if in current workspace)
if [ -d ".cursor/rules" ]; then
    echo "✅ Copying workspace rules..."
    mkdir -p "$OUTPUT_DIR/workspace-rules"
    cp -r .cursor/rules/* "$OUTPUT_DIR/workspace-rules/" 2>/dev/null
fi

# Create install script
cat > "$OUTPUT_DIR/install.sh" << 'EOF'
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
EOF

chmod +x "$OUTPUT_DIR/install.sh"

echo ""
echo "✨ Export complete!"
echo "📁 Configuration saved to: $OUTPUT_DIR"
echo ""
echo "To install on another machine:"
echo "  1. Copy the entire '$OUTPUT_DIR' folder to the new machine"
echo "  2. Run: cd $OUTPUT_DIR && ./install.sh"
echo ""
