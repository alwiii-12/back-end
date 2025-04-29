from flask import Flask, request, jsonify
from flask_cors import CORS
import pandas as pd
import os

app = Flask(__name__)
CORS(app)

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files['file']

    try:
        df = pd.read_excel(file)
    except Exception as e:
        return jsonify({"error": f"Error reading Excel file: {str(e)}"}), 500

    results = []

    for index, row in df.iterrows():
        # Adjusted to support your actual column names
        date = row.get("Date") or row.get("date") or row.get("Measurement Date")
        variation = row.get("Output (%)") or row.get("variation") or row.get("Output Variation")

        if date is None or variation is None:
            continue

        try:
            variation = float(variation)
        except ValueError:
            continue

        # Evaluation logic with warning margin
        status = "Pass"
        if abs(variation) > 2:
            status = "Fail"
        elif abs(variation) >= 1.8:
            status = "Warning"

        results.append({
            "date": str(date),
            "variation": variation,
            "status": status
        })

    return jsonify(results)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
