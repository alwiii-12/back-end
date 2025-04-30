from flask import Flask, request, jsonify
from flask_cors import CORS
import sqlite3
import os
from datetime import datetime

app = Flask(__name__)
CORS(app)

DB_NAME = "linac_data.db"
TOLERANCE = 2.0

def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS qa_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            energy TEXT,
            date TEXT,
            variation REAL,
            status TEXT,
            month TEXT,
            year INTEGER
        )''')

init_db()

def evaluate_status(value):
    if value is None:
        return "N/A"
    val = abs(value)
    if val < TOLERANCE:
        return "Within Tolerance"
    elif val == TOLERANCE:
        return "Warning"
    else:
        return "Out of Tolerance"

@app.route('/save', methods=['POST'])
def save_data():
    data = request.get_json()
    month = data['month']
    year = data['year']
    headers = data['headers'][1:]
    rows = data['rows']

    try:
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()

            # Optional: clear old data for this month
            cursor.execute("DELETE FROM qa_data WHERE month = ? AND year = ?", (month, year))

            for row in rows:
                energy = row[0]
                for i, val in enumerate(row[1:]):
                    if val is None or val == '':
                        continue
                    date_str = f"{headers[i]}-{year}"
                    try:
                        date_obj = datetime.strptime(date_str, "%d-%b-%Y")
                        status = evaluate_status(float(val))
                        cursor.execute(
                            '''INSERT INTO qa_data (energy, date, variation, status, month, year)
                               VALUES (?, ?, ?, ?, ?, ?)''',
                            (energy, date_obj.date().isoformat(), val, status, month, year)
                        )
                    except Exception as e:
                        print(f"Skipping invalid cell: {val} at {headers[i]}: {e}")

        return jsonify({"message": "Data saved successfully."}), 200

    except Exception as e:
        print("Save error:", e)
        return jsonify({"error": str(e)}), 500

@app.route('/load', methods=['POST'])
def load_data():
    data = request.get_json()
    month = data.get('month')
    year = data.get('year')

    try:
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''SELECT energy, date, variation FROM qa_data
                   WHERE month = ? AND year = ?''',
                (month, year)
            )
            rows = cursor.fetchall()

        structured = {}
        for energy, date_str, val in rows:
            date_obj = datetime.strptime(date_str, "%Y-%m-%d")
            label = date_obj.strftime("%d-%b")
            if energy not in structured:
                structured[energy] = {}
            structured[energy][label] = val

        return jsonify(structured)

    except Exception as e:
        print("Load error:", e)
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)
