#!/bin/bash

# Exit immediately if a command exits with a non-zero status.
set -e

echo "Running custom post_deploy.sh script..."

# 1. Install Python dependencies from requirements.txt
echo "Installing Python dependencies from requirements.txt..."
pip install -r requirements.txt
echo "Python dependencies installed."

# 2. Download the SpaCy English model to /tmp
echo "Downloading SpaCy model en_core_web_sm to /tmp/spacy_models/..."
# Define a variable for clarity - we'll download directly into /tmp for better runtime access
SPACY_DOWNLOAD_TARGET_DIR="/tmp/spacy_models" # NEW: Use /tmp
mkdir -p "${SPACY_DOWNLOAD_TARGET_DIR}" # Ensure the directory exists

python -m spacy download en_core_web_sm --data-path "${SPACY_DOWNLOAD_TARGET_DIR}"

# Check if the download was successful
MODEL_ACTUAL_DIR="${SPACY_DOWNLOAD_TARGET_DIR}/en_core_web_sm" # The model itself will be in a subdirectory
if [ -d "${MODEL_ACTUAL_DIR}" ]; then
    echo "SpaCy model downloaded successfully to ${MODEL_ACTUAL_DIR}."
else
    echo "ERROR: SpaCy model download failed or directory not found in ${MODEL_ACTUAL_DIR}."
    exit 1 # Exit with error if model is not there
fi

echo "post_deploy.sh script finished."
