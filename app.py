from flask import Flask, request, jsonify
from flask_cors import CORS
import sqlite3

app = Flask(__name__)
CORS(app)

DB = "linac_data.db"

def init_db():
    with sqlite3.connect(DB) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS output (
                date TEXT,
                energy TEXT,
                variation REAL,
                PRIMARY KEY (date, energy)
            )
        """)

@app.route('/save-entry', methods=['POST'])
def save_entry():
    data = request.get_json()
    with sqlite3.connect(DB) as conn:
        conn.execute("""
            INSERT OR REPLACE INTO output (date, energy, variation)
            VALUES (?, ?, ?)
        """, (data['date'], data['energy'], data['variation']))
    return jsonify({"status": "success"})

@app.route('/get-data')
def get_data():
    year = request.args.get('year')
    month = request.args.get('month').zfill(2)
    with sqlite3.connect(DB) as conn:
        cursor = conn.execute("SELECT date, energy, variation FROM output WHERE date LIKE ?", (f"{year}-{month}-%",))
        entries = [{"date": r[0], "energy": r[1], "variation": r[2]} for r in cursor.fetchall()]
    return jsonify({"data": entries})

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=10000)
