from flask import Flask, request, jsonify
from flask_cors import CORS
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
import json
import logging
from calendar import monthrange

# Firebase Admin SDK
import firebase_admin
from firebase_admin import credentials, firestore

app = Flask(__name__)
CORS(app)
app.logger.setLevel(logging.DEBUG)

# === Configuration for Email Alerts ===
SENDER_EMAIL = 'itsmealwin12@gmail.com'
RECEIVER_EMAIL = 'alwinjose812@gmail.com'
APP_PASSWORD = 'tjvy ksue rpnk xmaf'  # Use Render Secret in production

# === Firebase Firestore Initialization ===
firebase_json = os.environ.get("FIREBASE_CREDENTIALS")
if not firebase_json:
    raise Exception("FIREBASE_CREDENTIALS environment variable not set.")

try:
    firebase_dict = json.loads(firebase_json)
    cred = credentials.Certificate(firebase_dict)
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    app.logger.info("✅ Firebase initialized.")
except Exception as e:
    app.logger.error("🔥 Firebase init failed: %s", str(e))
    raise

# === Constant Energy Rows ===
ENERGY_TYPES = ["6X", "10X", "15X", "6X FFF", "10X FFF", "6E", "9E", "12E", "15E", "18E"]

# === Endpoint to Save QA Data (2D array from frontend) ===
@app.route('/save', methods=['POST'])
def save_data():
    try:
        content = request.get_json(force=True)
        app.logger.info("📥 Save request: %s", content)

        if 'month' not in content or 'data' not in content:
            return jsonify({'status': 'error', 'message': 'Missing "month" or "data"'}), 400

        month = f"Month_{content['month']}"
        raw_data = content['data']

        formatted_data = []
        for row in raw_data:
            if not row or len(row) < 2:
                continue
            energy = str(row[0]).strip()
            values = [str(v).strip() for v in row[1:]]
            formatted_data.append({
                "energy": energy,
                "values": values
            })

        db.collection('linac_data').document(month).set({'data': formatted_data}, merge=True)
        app.logger.info("✅ Data saved for month: %s", month)
        return jsonify({'status': 'success'}), 200

    except Exception as e:
        app.logger.error("❌ Save failed: %s", str(e), exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500

# === Endpoint to Load QA Data (converted back to 2D array) ===
@app.route('/data', methods=['GET'])
def get_data():
    month_param = request.args.get('month')
    if not month_param:
        return jsonify({'error': 'Missing month parameter'}), 400

    doc_id = f"Month_{month_param}"
    try:
        doc = db.collection('linac_data').document(doc_id).get()
        if doc.exists:
            data = doc.to_dict()
            structured = data.get('data', [])
            locked = data.get('locked', False)

            # Convert from list of dicts to 2D array
            if isinstance(structured, list) and all(isinstance(row, dict) for row in structured):
                table = []
                for entry in structured:
                    row = [entry.get('energy', '')] + entry.get('values', [])
                    table.append(row)
                return jsonify({'data': table, 'locked': locked})

        # Return default 2D array if not found
        year, mon = month_param.split("-")
        _, num_days = monthrange(int(year), int(mon))
        default_data = [[energy] + [""] * num_days for energy in ENERGY_TYPES]
        app.logger.info("📁 Returning blank 2D data for %s", doc_id)
        return jsonify({'data': default_data, 'locked': False})

    except Exception as e:
        app.logger.error("❌ Load failed: %s", str(e), exc_info=True)
        return jsonify({'error': str(e)}), 500

# === Endpoint to Lock/Unlock Monthly Data ===
@app.route('/lock', methods=['POST'])
def lock_data():
    try:
        content = request.get_json(force=True)
        if 'month' not in content or 'locked' not in content:
            return jsonify({'status': 'error', 'message': 'Missing "month" or "locked"'}), 400

        month = f"Month_{content['month']}"
        locked = content['locked']
        db.collection('linac_data').document(month).set({'locked': locked}, merge=True)
        app.logger.info("🔒 Lock status updated for %s: %s", month, locked)
        return jsonify({'status': 'success'}), 200

    except Exception as e:
        app.logger.error("❌ Lock update failed: %s", str(e), exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500

# === Endpoint to Send Out-of-Tolerance Alerts ===
@app.route('/send-alert', methods=['POST'])
def send_alert():
    try:
        content = request.get_json(force=True)
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

        server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        server.login(SENDER_EMAIL, APP_PASSWORD)
        server.send_message(msg)
        server.quit()

        app.logger.info("📧 Alert email sent successfully.")
        return jsonify({'status': 'alert sent'})

    except Exception as e:
        app.logger.error("❌ Email error: %s", str(e), exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500

# === Default Route ===
@app.route('/')
def index():
    return "✅ LINAC QA Backend is running."

# === Main Entry Point ===
if __name__ == '__main__':
    app.run(debug=True)
