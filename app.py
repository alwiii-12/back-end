from flask import Flask, request, jsonify
from flask_cors import CORS
import sqlite3
import os

app = Flask(__name__)
CORS(app)

DB_FILE = 'linac_data.db'

def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS output_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                month TEXT NOT NULL,
                data TEXT NOT NULL
            )
        ''')
        conn.commit()

@app.route('/save', methods=['POST'])
def save_data():
    try:
        content = request.get_json()
        month = content.get('month')
        data = content.get('data')

        if not month or not data:
            return jsonify({'message': 'Invalid data'}), 400

        with sqlite3.connect(DB_FILE) as conn:
            c = conn.cursor()
            c.execute("SELECT id FROM output_data WHERE month = ?", (month,))
            existing = c.fetchone()

            if existing:
                c.execute("UPDATE output_data SET data = ? WHERE month = ?", (data, month))
            else:
                c.execute("INSERT INTO output_data (month, data) VALUES (?, ?)", (month, data))
            conn.commit()

        return jsonify({'message': 'Data saved successfully'})
    except Exception as e:
        return jsonify({'message': f'Error: {str(e)}'}), 500

@app.route('/load', methods=['GET'])
def load_data():
    try:
        month = request.args.get('month')
        if not month:
            return jsonify({'message': 'Month parameter missing'}), 400

        with sqlite3.connect(DB_FILE) as conn:
            c = conn.cursor()
            c.execute("SELECT data FROM output_data WHERE month = ?", (month,))
            row = c.fetchone()
            if row:
                return jsonify({'data': row[0]})
            else:
                return jsonify({'data': None})
    except Exception as e:
        return jsonify({'message': f'Error: {str(e)}'}), 500

if __name__ == '__main__':
    if not os.path.exists(DB_FILE):
        init_db()
    app.run(debug=True)
