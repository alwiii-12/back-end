from flask import Flask, request, jsonify
from flask_cors import CORS
import sqlite3
import json
import os

app = Flask(__name__)
CORS(app)

DB_NAME = "linac_data.db"

def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS monthly_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                month TEXT UNIQUE,
                data TEXT
            )
        """)

@app.route('/save-data', methods=['POST'])
def save_data():
    try:
        content = request.get_json()
        month = content.get("month")
        data = content.get("data")

        if not month or not data:
            return jsonify({"error": "Missing month or data"}), 400

        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT OR REPLACE INTO monthly_data (month, data) VALUES (?, ?)", (month, json.dumps(data)))
            conn.commit()

        return jsonify({"message": "Data saved successfully"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/load-data', methods=['GET'])
def load_data():
    try:
        month = request.args.get("month")
        if not month:
            return jsonify({"error": "Missing month parameter"}), 400

        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT data FROM monthly_data WHERE month = ?", (month,))
            row = cursor.fetchone()

        if row:
            return jsonify({"data": json.loads(row[0])})
        else:
            return jsonify({"data": []})  # no data yet

    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    if not os.path.exists(DB_NAME):
        init_db()
    else:
        init_db()
    app.run(debug=True)
