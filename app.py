from flask import Flask, request, jsonify
from flask_cors import CORS
import sqlite3
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import gspread
import os
import json
from google.oauth2 import service_account

app = Flask(__name__)
CORS(app)

DB_NAME = 'linac_data.db'
SENDER_EMAIL = 'itsmealwin12@gmail.com'
RECEIVER_EMAIL = 'alwinjose812@gmail.com'
APP_PASSWORD = 'tjvy ksue rpnk xmaf'

# === Initialize database ===
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS output_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            month TEXT NOT NULL,
            data TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# === Save QA data to SQLite ===
@app.route('/save', methods=['POST'])
def save_data():
    content = request.get_json()
    month = content['month']
    data = str(content['data'])

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM output_data WHERE month = ?', (month,))
    cursor.execute('INSERT INTO output_data (month, data) VALUES (?, ?)', (month, data))
    conn.commit()
    conn.close()
    return jsonify({'status': 'success'})

# === Load QA data from SQLite ===
@app.route('/data', methods=['GET'])
def get_data():
    month = request.args.get('month')
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT data FROM output_data WHERE month = ?', (month,))
    row = cursor.fetchone()
    conn.close()
    return jsonify({'data': eval(row[0]) if row else []})

# === Email alert for failed values ===
@app.route('/send-alert', methods=['POST'])
def send_alert():
    content = request.get_json()
    out_values = content.get('outValues', [])
    if not out_values:
        return jsonify({'status': 'no alerts sent'})

    message_body = "The following LINAC QA output values are out of tolerance (±2.0%):\n\n"
    for val in out_values:
        message_body += f"Energy: {val['energy']}, Date: {val['date']}, Value: {val['value']}%\n"

    msg = MIMEMultipart()
    msg['From'] = SENDER_EMAIL
    msg['To'] = RECEIVER_EMAIL
    msg['Subject'] = '⚠ LINAC QA Output Failed Alert'

    msg.attach(MIMEText(message_body, 'plain'))

    try:
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        server.login(SENDER_EMAIL, APP_PASSWORD)
        server.send_message(msg)
        server.quit()
        return jsonify({'status': 'alert sent'})
    except Exception as e:
        print("Email sending error:", e)
        return jsonify({'status': 'error', 'message': str(e)}), 500

# === Google Sheets setup ===
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
service_account_info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_KEY"])
creds = service_account.Credentials.from_service_account_info(service_account_info, scopes=scope)
client = gspread.authorize(creds)

# === ✅ Save QA data to correct monthly Google Sheet ===
@app.route('/save-google-sheets', methods=['POST'])
def save_data_to_google_sheets():
    try:
        data = request.json  # Expecting keys: headers, rows, month

        if not data:
            return jsonify({"error": "No data received"}), 400

        headers = data.get("headers")
        rows = data.get("rows")
        month = data.get("month")

        if not headers or not rows or not month:
            return jsonify({"error": "Missing headers, rows, or month"}), 400

        spreadsheet = client.open("LINAC_QA_Data")
        sheet_name = f"Month_{month}"

        try:
            sheet = spreadsheet.worksheet(sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            sheet = spreadsheet.add_worksheet(title=sheet_name, rows="100", cols="100")

        # Clear and update
        sheet.clear()
        sheet.insert_row(headers, 1)

        for i, row in enumerate(rows, start=2):
            sheet.insert_row(row, i)

        return jsonify({"message": f"Google Sheet '{sheet_name}' updated successfully!"}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)
