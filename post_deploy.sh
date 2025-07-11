#!/bin/bash

# Exit immediately if a command exits with a non-zero status.
set -e

echo "Running custom post_deploy.sh script..."

# 1. Install Python dependencies from requirements.txt
echo "Installing Python dependencies from requirements.txt..."
pip install -r requirements.txt
echo "Python dependencies installed."

# 2. Download the SpaCy English model
# Using --data-path to ensure it's saved in a writable and discoverable location within the venv
echo "Downloading SpaCy model en_core_web_sm..."
python -m spacy download en_core_web_sm --data-path .venv/share/spacy

# Check if the download was successful (optional, set -e should handle it)
if [ -d ".venv/share/spacy/en_core_web_sm" ]; then
    echo "SpaCy model downloaded successfully to .venv/share/spacy."
else
    echo "ERROR: SpaCy model download failed or directory not found."
    exit 1 # Exit with error if model is not there
fi

echo "post_deploy.sh script finished."
