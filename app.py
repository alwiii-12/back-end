
from flask import Flask, request, jsonify
from flask_cors import CORS
from datetime import datetime

app = Flask(__name__)
CORS(app)

results = []

@app.route('/submit', methods=['POST'])
def submit():
    data = request.get_json()
    output = float(data['output'])
    within_tolerance = 0.97 <= output <= 1.03

    record = {
        'date': data['date'],
        'machine': data['machine'],
        'output': output,
        'within_tolerance': within_tolerance,
        'comments': data.get('comments', ''),
        'timestamp': datetime.now().isoformat()
    }
    results.append(record)

    return jsonify({
        'message': f"{'✅ Within' if within_tolerance else '❌ Out'} of tolerance",
        'within_tolerance': within_tolerance
    })

@app.route('/results', methods=['GET'])
def get_results():
    return jsonify(results)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
