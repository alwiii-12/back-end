from flask import Flask, request, jsonify
from flask_cors import CORS
import pandas as pd

app = Flask(__name__)
CORS(app)

def evaluate_status(variation):
    if abs(variation) > 2:
        return "Fail"
    elif abs(variation) > 1.5:
        return "Warning"
    else:
        return "Pass"

@app.route("/upload", methods=["POST"])
def upload_file():
    if "file" not in request.files:
        return jsonify({"error": "No file part"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No selected file"}), 400

    try:
        df = pd.read_excel(file)
        results = []

        for _, row in df.iterrows():
            date = row.get("Date") or row.get("date")
            variation = row.get("Output Variation") or row.get("variation")

            if date is None or variation is None:
                continue

            status = evaluate_status(variation)

            results.append({
                "date": str(date),
                "variation": variation,
                "status": status
            })

        return jsonify(results)

    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True)
