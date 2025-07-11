#!/bin/bash

# Exit immediately if a command exits with a non-zero status.
set -e

echo "Running custom post_deploy.sh script..."

# 1. Install Python dependencies from requirements.txt
echo "Installing Python dependencies from requirements.txt..."
pip install -r requirements.txt
echo "Python dependencies installed."

# 2. Download the SpaCy English model to a designated data path
echo "Downloading SpaCy model en_core_web_sm..."
SPACY_DOWNLOAD_PATH=".venv/share/spacy" # Define a variable for clarity
python -m spacy download en_core_web_sm --data-path "${SPACY_DOWNLOAD_PATH}"

# Check if the download was successful
if [ -d "${SPACY_DOWNLOAD_PATH}/en_core_web_sm" ]; then
    echo "SpaCy model downloaded successfully to ${SPACY_DOWNLOAD_PATH}."
else
    echo "ERROR: SpaCy model download failed or directory not found in ${SPACY_DOWNLOAD_PATH}."
    exit 1 # Exit with error if model is not there
fi

# 3. Create a symlink to the SpaCy model within the site-packages directory
# This makes the model discoverable by spacy.load() without relying on SPACY_DATA env var or specific load paths.

echo "Creating symlink for SpaCy model into site-packages..."

# Find the Python site-packages directory within the virtual environment
# We assume the venv is located at /opt/render/project/src/.venv
SITE_PACKAGES_DIR=$(python -c "import site; print(site.getsitepackages()[0])")

# Full path to the downloaded model's actual directory
MODEL_SOURCE_DIR="${SPACY_DOWNLOAD_PATH}/en_core_web_sm"

# Determine the target symlink name (often the model name itself)
# This will be created inside SITE_PACKAGES_DIR
MODEL_SYMLINK_NAME="en_core_web_sm"
MODEL_DEST_PATH="${SITE_PACKAGES_DIR}/${MODEL_SYMLINK_NAME}"

# Remove existing symlink/directory if it somehow exists to prevent errors
rm -rf "${MODEL_DEST_PATH}"

# Create the symlink
ln -s "$(realpath "${MODEL_SOURCE_DIR}")" "${MODEL_DEST_PATH}"

if [ -L "${MODEL_DEST_PATH}" ]; then
    echo "Symlink created successfully: ${MODEL_DEST_PATH} -> $(realpath "${MODEL_SOURCE_DIR}")"
else
    echo "ERROR: Failed to create symlink for SpaCy model."
    exit 1
fi

echo "post_deploy.sh script finished."
