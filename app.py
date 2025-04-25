from flask import Flask, request, jsonify
from flask_cors import CORS
import pandas as pd
from werkzeug.utils import secure_filename
import os

app = Flask(__name__)
CORS(app)

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({"error": "No file part in request"}), 400

    file = request.files['file']

    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400

    if not file.filename.endswith(('.xlsx', '.xls')):
        return jsonify({"error": "Only Excel files are supported"}), 400

    try:
        df = pd.read_excel(file)

        if 'Variation' not in df.columns or 'Date' not in df.columns:
            return jsonify({"error": "Excel must contain 'Date' and 'Variation' columns"}), 400

        results = []
        for _, row in df.iterrows():
            variation = row['Variation']
            result = {
                'date': str(row['Date']),
                'variation': variation,
                'within_tolerance': -2 <= variation <= 2
            }
            results.append(result)

        return jsonify(results)

    except Exception as e:
        return jsonify({"error": f"Invalid Excel file or format: {str(e)}"}), 400

if __name__ == '__main__':
    app.run(debug=True)
