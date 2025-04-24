# backend/app.py
from flask import Flask, request, jsonify
from flask_cors import CORS
from datetime import datetime
import pandas as pd
import pytz
import io

app = Flask(__name__)
CORS(app)

results = []

tz = pytz.timezone('Asia/Kolkata')

@app.route('/submit', methods=['POST'])
def submit():
    data = request.get_json()
    output = float(data['output'])
    within_tolerance = 0.98 <= output <= 1.02

    record = {
        'date': data['date'],
        'machine': data['machine'],
        'output': output,
        'within_tolerance': within_tolerance,
        'comments': data.get('comments', ''),
        'timestamp': datetime.now(tz).isoformat()
    }
    results.append(record)

    return jsonify({
        'message': '✅ Within tolerance' if within_tolerance else '❌ Out of tolerance',
        'within_tolerance': within_tolerance
    })

@app.route('/upload', methods=['POST'])
def upload_excel():
    file = request.files['file']
    df = pd.read_excel(file)
    output_results = []

    for index, row in df.iterrows():
        variation = row['Variation']
        within_tolerance = -0.02 <= variation <= 0.02

        output_results.append({
            'date': row.get('Date', f'Row {index+1}'),
            'variation': variation,
            'within_tolerance': within_tolerance,
            'timestamp': datetime.now(tz).isoformat()
        })

    return jsonify(output_results)

@app.route('/results', methods=['GET'])
def get_results():
    return jsonify(results)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
