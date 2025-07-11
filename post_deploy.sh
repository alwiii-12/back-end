#!/bin/bash

# Exit immediately if a command exits with a non-zero status.
set -e

echo "Running custom post_deploy.sh script..."

# 1. Install Python dependencies from requirements.txt
echo "Installing Python dependencies from requirements: [1]"
pip install -r requirements.txt
echo "Python dependencies installed."

# 2. Download the SpaCy English model to a designated data path
echo "Downloading SpaCy model en_core_web_sm..."
# Define a variable for clarity - this is the target directory for the download
SPACY_DOWNLOAD_TARGET_DIR=".venv/share/spacy"
python -m spacy download en_core_web_sm --data-path "${SPACY_DOWNLOAD_TARGET_DIR}"

# Check if the download was successful
MODEL_ACTUAL_DIR="${SPACY_DOWNLOAD_TARGET_DIR}/en_core_web_sm"
if [ -d "${MODEL_ACTUAL_DIR}" ]; then
    echo "SpaCy model downloaded successfully to ${MODEL_ACTUAL_DIR}."
else
    echo "ERROR: SpaCy model download failed or directory not found in ${MODEL_ACTUAL_DIR}."
    exit 1 # Exit with error if model is not there
fi

# NEW STEP: Copy the downloaded SpaCy model to a well-known location accessible at runtime
# This is a highly robust way to ensure the model is available.
# We'll put it directly under /opt/render/project/src/spacy_models/
echo "Copying SpaCy model to /opt/render/project/src/spacy_models/ for runtime access..."

# Define the target runtime directory for the model
RUNTIME_SPACY_DIR="/opt/render/project/src/spacy_models"
mkdir -p "${RUNTIME_SPACY_DIR}" # Ensure the directory exists

# Copy the entire downloaded model directory into the runtime directory
# Use -R for recursive copy (to copy contents of directory)
cp -R "${MODEL_ACTUAL_DIR}" "${RUNTIME_SPACY_DIR}/"

# Verify the model is present in the runtime directory
if [ -d "${RUNTIME_SPACY_DIR}/en_core_web_sm" ]; then
    echo "SpaCy model successfully copied to ${RUNTIME_SPACY_DIR}/en_core_web_sm."
else
    echo "ERROR: SpaCy model copy failed to ${RUNTIME_SPACY_DIR}/en_core_web_sm."
    exit 1
fi

echo "post_deploy.sh script finished."
