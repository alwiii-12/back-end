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
SPACY_DOWNLOAD_TARGET_DIR="/tmp/spacy_models"
mkdir -p "${SPACY_DOWNLOAD_TARGET_DIR}" # Ensure the directory exists

python -m spacy download en_core_web_sm --data-path "${SPACY_DOWNLOAD_TARGET_DIR}"

MODEL_ACTUAL_DIR="${SPACY_DOWNLOAD_TARGET_DIR}/en_core_web_sm"

# VERIFICATION STEP: Check if the model directory EXISTS immediately after download
echo "Verifying SpaCy model directory immediately after download in post_deploy.sh..."
if [ -d "${MODEL_ACTUAL_DIR}" ]; then
    echo "SUCCESS: SpaCy model directory EXISTS at ${MODEL_ACTUAL_DIR} in post_deploy.sh."
    ls -la "${MODEL_ACTUAL_DIR}" # List contents to confirm
else
    echo "ERROR: SpaCy model directory NOT FOUND at ${MODEL_ACTUAL_DIR} in post_deploy.sh. This is a critical download failure."
    exit 1 # Exit with error if model is not there
fi

# Optional: Try a quick load test in post_deploy.sh to confirm it's usable for SpaCy
echo "Running quick SpaCy load test in post_deploy.sh..."
if python -c "import spacy; nlp = spacy.load('${MODEL_ACTUAL_DIR}'); print('SpaCy model loaded successfully in post_deploy.sh test.')" > /dev/null 2>&1; then
    echo "SUCCESS: SpaCy model loaded successfully during post_deploy.sh test."
else
    echo "WARNING: SpaCy model did NOT load during post_deploy.sh test, but files exist. Proceeding."
fi


echo "post_deploy.sh script finished."
