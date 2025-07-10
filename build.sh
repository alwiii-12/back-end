#!/bin/bash

# Exit immediately if a command exits with a non-zero status.
set -e

echo "Running custom build.sh script..."

# 1. Install Python dependencies from requirements.txt
pip install -r requirements.txt

# 2. Download the SpaCy English model
# We use a specific target directory that Render should allow writing to.
# The default spacy download location might be restricted.
# This makes sure the model is available when the app starts.
python -m spacy download en_core_web_sm --data-path .venv/share/spacy

# Check if the download was successful (optional, as set -e handles failures)
if [ -d ".venv/share/spacy/en_core_web_sm" ]; then
    echo "SpaCy model downloaded successfully to .venv/share/spacy."
else
    echo "ERROR: SpaCy model download failed or directory not found."
    exit 1 # Exit with error if model is not there
fi

echo "Custom build.sh script finished."
