#!/bin/bash
set -e

# Load environment variables
if [ -f ".env" ]; then
    export $(cat .env | grep -v '^#' | xargs)
fi

if [ -z "$GITHUB_TOKEN" ] || [ -z "$GITHUB_REPO_URL" ]; then
    echo "Error: GITHUB_TOKEN and GITHUB_REPO_URL must be set in .env"
    exit 1
fi

# Extract repo info from URL
# https://github.com/user/repo.git -> user/repo
repo_path=$(echo "$GITHUB_REPO_URL" | sed 's|https://github.com/||' | sed 's|.git$||')

# Construct authenticated URL
auth_url="https://${GITHUB_TOKEN}@github.com/${repo_path}.git"

TARGET_DIR="${TARGET_REPO_PATH:-./target-repo}"

# Clone or update repo
if [ -d "$TARGET_DIR/.git" ]; then
    echo "Updating existing repo..."
    cd "$TARGET_DIR"
    git pull origin main || git pull origin master
else
    echo "Cloning repo..."
    rm -rf "$TARGET_DIR"
    git clone "$auth_url" "$TARGET_DIR"
fi

# Configure git for commits
cd "$TARGET_DIR"
git config user.email "voice-agent@render.com"
git config user.name "Render Voice Agent"

echo "✓ Target repo ready at $TARGET_DIR"
