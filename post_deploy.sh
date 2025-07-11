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
MODEL_DIR="${SPACY_DOWNLOAD_PATH}/en_core_web_sm" # This is the directory containing the downloaded model
if [ -d "${MODEL_DIR}" ]; then
    echo "SpaCy model downloaded successfully to ${MODEL_DIR}."
else
    echo "ERROR: SpaCy model download failed or directory not found in ${MODEL_DIR}."
    exit 1 # Exit with error if model is not there
fi

# 3. Create a symbolic link using 'spacy link'
# This tells SpaCy how to find the model by creating an entry point in site-packages.
echo "Creating SpaCy model link using 'spacy link'..."
# The 'spacy link' command expects the path to the model directory and the desired link name.
# The link name is typically the model's full name (e.g., 'en_core_web_sm').
# We are linking the downloaded model from MODEL_DIR to 'en_core_web_sm' within the active Python env.
python -m spacy link "${MODEL_DIR}" "en_core_web_sm" --force

# Verify the link was created and the model is now discoverable
if python -c "import spacy; spacy.load('en_core_web_sm')"; then
    echo "SpaCy model 'en_core_web_sm' successfully linked and discoverable via spacy.load()."
else
    echo "ERROR: SpaCy model 'en_core_web_sm' not discoverable after linking."
    exit 1
fi

echo "post_deploy.sh script finished."
