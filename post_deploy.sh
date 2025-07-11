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
MODEL_DIR="${SPACY_DOWNLOAD_PATH}/en_core_web_sm"
if [ -d "${MODEL_DIR}" ]; then
    echo "SpaCy model downloaded successfully to ${MODEL_DIR}."
else
    echo "ERROR: SpaCy model download failed or directory not found in ${MODEL_DIR}."
    exit 1 # Exit with error if model is not there
fi

# 3. Pip install the downloaded SpaCy model
# This is the most robust way to ensure spacy.load() finds it at runtime.
echo "Pip installing the downloaded SpaCy model..."
pip install "${MODEL_DIR}"

# Check if the pip install was successful (pip install usually returns non-zero on failure, caught by set -e)
# We can also check if the model is now discoverable by spacy directly
if python -c "import spacy; spacy.load('en_core_web_sm')"; then
    echo "SpaCy model 'en_core_web_sm' successfully installed and discoverable."
else
    echo "ERROR: SpaCy model 'en_core_web_sm' not discoverable after pip install."
    exit 1
fi

echo "post_deploy.sh script finished."
