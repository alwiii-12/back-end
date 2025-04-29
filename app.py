from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import pandas as pd
import sqlite3
import io
import os

app = Flask(__name__)
CORS(app)

# Initialize SQLite database
def init_db():
    conn = sqlite3.connect("evaluations.db")
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            variation TEXT NOT NULL,
            status TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# Route: Upload Excel file
@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400

    try:
        df = pd.read_excel(file)

        if "Date" not in df.columns or "Variation" not in df.columns:
            return jsonify({"error": "Excel must contain 'Date' and 'Variation' columns."}), 400

        evaluation_results = []
        for _, row in df.iterrows():
            date = str(row["Date"]).split(" ")[0]  # get only date part
            variation = row["Variation"]
            status = ""

            if abs(variation) <= 0.02:
                status = "Pass"
            elif abs(variation) <= 0.03:
                status = "Warning"
            else:
                status = "Fail"

            result = {
                "date": date,
                "variation": f"{variation:.1%}",
                "status": status
            }
            evaluation_results.append(result)

        # Save to SQLite database
        conn = sqlite3.connect("evaluations.db")
        cursor = conn.cursor()
        cursor.execute("DELETE FROM evaluations")  # Clear old data (optional)
        for entry in evaluation_results:
            cursor.execute(
                'INSERT INTO evaluations (date, variation, status) VALUES (?, ?, ?)',
                (entry['date'], entry['variation'], entry['status'])
            )
        conn.commit()
        conn.close()

        return jsonify({"results": evaluation_results})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Route: Download current evaluation results as CSV
@app.route('/download_csv', methods=['POST'])
def download_csv():
    data = request.get_json()
    df = pd.DataFrame(data["results"])
    csv_buffer = io.StringIO()
    df.to_csv(csv_buffer, index=False)

    return Response(
        csv_buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-disposition": "attachment; filename=evaluation_results.csv"}
    )

# Route: Download saved results from database
@app.route('/download_saved_csv')
def download_saved_csv():
    conn = sqlite3.connect("evaluations.db")
    cursor = conn.cursor()
    cursor.execute("SELECT date, variation, status FROM evaluations")
    rows = cursor.fetchall()
    conn.close()

    df = pd.DataFrame(rows, columns=["Date", "Variation", "Status"])

    csv_buffer = io.StringIO()
    df.to_csv(csv_buffer, index=False)

    return Response(
        csv_buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-disposition": "attachment; filename=saved_evaluation_results.csv"}
    )

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host='0.0.0.0', port=port)
