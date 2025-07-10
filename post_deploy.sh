#!/bin/bash

# Activate the virtual environment (if Render uses one implicitly)
# Render's Python runtime usually handles this, but it's good practice
# source .venv/bin/activate # Uncomment if you manually manage venv

# Download the small English model for SpaCy
python -m spacy download en_core_web_sm

echo "SpaCy model downloaded successfully."
