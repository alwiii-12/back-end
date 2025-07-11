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
SPACY_DOWNLOAD_TARGET_DIR=".venv/share/spacy" # Define a variable for clarity
python -m spacy download en_core_web_sm --data-path "${SPACY_DOWNLOAD_TARGET_DIR}"

# Check if the download was successful
MODEL_ACTUAL_DIR="${SPACY_DOWNLOAD_TARGET_DIR}/en_core_web_sm" # This is the directory containing the downloaded model
if [ -d "${MODEL_ACTUAL_DIR}" ]; then
    echo "SpaCy model downloaded successfully to ${MODEL_ACTUAL_DIR}."
else
    echo "ERROR: SpaCy model download failed or directory not found in ${MODEL_ACTUAL_DIR}."
    exit 1 # Exit with error if model is not there
fi

# NEW STEP: Install the downloaded SpaCy model as an editable package using pip.
# This ensures it's properly registered in the Python environment.
echo "Installing SpaCy model as an editable package: pip install -e ${MODEL_ACTUAL_DIR}"
pip install -e "${MODEL_ACTUAL_DIR}"

# Verify that spacy.load() can find the model after installation
if python -c "import spacy; spacy.load('en_core_web_sm')"; then
    echo "SpaCy model 'en_core_web_sm' successfully installed and discoverable by spacy.load()."
else
    echo "ERROR: SpaCy model 'en_core_web_sm' not discoverable after pip install -e."
    exit 1
fi

echo "post_deploy.sh script finished."
