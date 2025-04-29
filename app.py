# Replace this block in your existing app.py

import pandas as pd
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

@app.route("/upload", methods=["POST"])
def upload_file():
    file = request.files["file"]
    df = pd.read_excel(file)

    # Adjust column names based on the Excel structure
    # Print for debugging
    print("Columns in uploaded file:", df.columns.tolist())

    results = []
    for index, row in df.iterrows():
        variation = row.get("Variation")  # <-- make sure this matches Excel column name
        date = row.get("Date")  # same here
        if pd.isna(variation) or pd.isna(date):
            continue

        status = "Pass"
        if abs(variation) > 2:
            status = "Fail"
        elif abs(variation) > 1:
            status = "Warning"

        results.append({
            "Date": str(date),
            "Variation": round(variation, 2),
            "Status": status,
        })

    return jsonify({"results": results})
