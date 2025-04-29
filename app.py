from flask import Flask, request, jsonify
from flask_cors import CORS
import pandas as pd

app = Flask(__name__)
CORS(app)

TOLERANCE = 2.0  # ±2%

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
        df = pd.read_excel(file)
        df.columns = df.columns.str.strip()  # Remove whitespace from column names
        print("Columns read from Excel:", df.columns.tolist())  # Debug line

        if 'Test' not in df.columns:
            return jsonify({'error': 'Excel file must contain a "Test" column'}), 400

        df = df.dropna(subset=['Test'])  # Ensure Test column has values

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
        print("Upload error:", str(e))  # Log to server for debugging
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)
