from flask import Flask, request, jsonify
from flask_cors import CORS
from datetime import datetime
import pandas as pd
import pytz

app = Flask(__name__)
CORS(app)

tz = pytz.timezone('Asia/Kolkata')

@app.route('/upload', methods=['POST'])
def upload_excel():
    file = request.files.get('file')
    if not file:
        return jsonify({'error': 'No file uploaded'}), 400

    try:
        df = pd.read_excel(file)
    except Exception as e:
        return jsonify({'error': 'Invalid Excel file'}), 400

    output_results = []
    for index, row in df.iterrows():
        variation = row.get('Variation')
        try:
            variation = float(variation)
            within_tolerance = -0.02 <= variation <= 0.02
        except:
            variation = None
            within_tolerance = False

        output_results.append({
            'date': row.get('Date', f'Row {index+1}'),
            'variation': variation,
            'within_tolerance': within_tolerance,
            'timestamp': datetime.now(tz).isoformat()
        })

    return jsonify(output_results)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
