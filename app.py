from flask import Flask, request, jsonify
from flask_cors import CORS
import pandas as pd
import datetime

app = Flask(__name__)
CORS(app)

TOLERANCE = 2.0  # ±2% threshold

def evaluate_status(variation):
    if pd.isna(variation):
        return "N/A"
    elif abs(variation) < TOLERANCE:
        return "Within Tolerance"
    elif abs(variation) == TOLERANCE:
        return "Warning"
    else:
        return "Out of Tolerance"

@app.route('/upload', methods=['POST'])
def upload_file():
    file = request.files.get('file')
    if not file:
        return jsonify({'error': 'No file provided'}), 400

    try:
        df = pd.read_excel(file, sheet_name=0)
        df = df.dropna(subset=['Test'])  # Ensure 'Test' (energy) exists

        results = []
        for _, row in df.iterrows():
            energy = row['Test']
            for col in df.columns[2:]:  # Skip 'Test' and 'Tolerance (%)'
                try:
                    date = pd.to_datetime(col).strftime('%Y-%m-%d')
                except:
                    date = str(col)
                variation = row[col]
                status = evaluate_status(variation)
                results.append({
                    'energy': energy,
                    'date': date,
                    'variation': round(variation, 2) if pd.notna(variation) else None,
                    'status': status
                })

        return jsonify(results)

    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)
