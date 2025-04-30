from flask import Flask, request, jsonify, session
from flask_cors import CORS
import sqlite3
import smtplib
from email.mime.text import MIMEText
import os

app = Flask(__name__)
app.secret_key = os.urandom(24)
CORS(app, supports_credentials=True)

DB_NAME = "linac_data.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS monthly_data (
            month TEXT PRIMARY KEY,
            data TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

# === Email config ===
SENDER_EMAIL = "itsmealwin12@gmail.com"
APP_PASSWORD = "tjvyksuerpnkxmaf"  # No spaces!
RECIPIENT_EMAIL = "alwinjose812@gmail.com"

def send_failure_email(failed_entries, month):
    if not failed_entries:
        return

    body = f"⚠️ QA Failures Detected for {month}:\n\n"
    for entry in failed_entries:
        body += f"- {entry['Energy']} on {entry['Date']} = {entry['Value']}% ❌\n"

    msg = MIMEText(body)
    msg["Subject"] = f"❌ LINAC QA Failure Alert - {month}"
    msg["From"] = SENDER_EMAIL
    msg["To"] = RECIPIENT_EMAIL

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(SENDER_EMAIL, APP_PASSWORD)
            smtp.send_message(msg)
            print("QA failure alert sent.")
    except Exception as e:
        print("Error sending email:", e)

@app.route('/')
def home():
    return "LINAC QA backend running."

@app.route('/save_month_data', methods=['POST'])
def save_month_data():
    content = request.get_json()
    month = content['month']
    data = content['data']

    # Check for failed values > 2.0%
    failed = []
    try:
        for row in data:
            energy = row[0]
            for i, val in enumerate(row[1:], start=1):
                try:
                    v = float(val)
                    if abs(v) > 2.0:
                        day = f"{month}-{str(i).zfill(2)}"
                        failed.append({"Energy": energy, "Date": day, "Value": v})
                except:
                    continue
    except Exception as e:
        print("Scan error:", e)

    # Send alert if needed
    send_failure_email(failed, month)

    # Save to DB
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("REPLACE INTO monthly_data (month, data) VALUES (?, ?)", (month, str(data)))
    conn.commit()
    conn.close()

    return jsonify({"message": "Data saved successfully."})

@app.route('/load_month_data', methods=['GET'])
def load_month_data():
    month = request.args.get("month")
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT data FROM monthly_data WHERE month = ?", (month,))
    row = c.fetchone()
    conn.close()
    if row:
        import ast
        return jsonify({"data": ast.literal_eval(row[0])})
    else:
        return jsonify({"data": None})

if __name__ == "__main__":
    app.run(debug=True)
