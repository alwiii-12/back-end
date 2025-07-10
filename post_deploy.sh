#!/bin/bash

# Exit immediately if a command exits with a non-zero status.
set -e

echo "Running custom post_deploy.sh script..."

# Download the small English model for SpaCy
# Using --data-path to ensure it's saved in a writable and discoverable location within the venv
python -m spacy download en_core_web_sm --data-path .venv/share/spacy

# Check if the download was successful (optional, set -e should handle it)
if [ -d ".venv/share/spacy/en_core_web_sm" ]; then
    echo "SpaCy model downloaded successfully to .venv/share/spacy."
else
    echo "ERROR: SpaCy model download failed or directory not found."
    exit 1 # Exit with error if model is not there
fi

echo "post_deploy.sh script finished."
