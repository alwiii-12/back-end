#!/bin/bash

# Exit immediately if a command exits with a non-zero status.
set -e

echo "Running custom post_deploy.sh script..."

# 1. Install Python dependencies from requirements.txt
echo "Installing Python dependencies from requirements.txt..."
pip install -r requirements.txt
echo "Python dependencies installed."

# REMOVED: SpaCy model download and verification steps.
# The application will no longer attempt to download or load a SpaCy NLP model.

echo "post_deploy.sh script finished."
