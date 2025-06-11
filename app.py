from flask import Flask, request, jsonify
from flask_cors import CORS
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
import json
import logging

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

# === Endpoint to Save QA Data into Firestore ===
@app.route('/save', methods=['POST'])
def save_data():
    try:
        content = request.get_json(force=True)
        app.logger.info("📥 Save request: %s", content)

        if 'month' not in content or 'data' not in content:
            return jsonify({'status': 'error', 'message': 'Missing "month" or "data"'}), 400

        month = content['month']
        data = content['data']

        # Save under "linac_data" collection with document ID = month
        db.collection('linac_data').document(month).set({'data': data})
        app.logger.info("✅ Data saved for month: %s", month)
        return jsonify({'status': 'success'}), 200

    except Exception as e:
        app.logger.error("❌ Save failed: %s", str(e), exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500

# === Endpoint to Load QA Data from Firestore ===
@app.route('/data', methods=['GET'])
def get_data():
    month = request.args.get('month')
    if not month:
        return jsonify({'error': 'Missing month parameter'}), 400

    try:
        doc_ref = db.collection('linac_data').document(month)
        doc = doc_ref.get()
        if doc.exists:
            app.logger.info("📤 Data loaded for %s", month)
            return jsonify({'data': doc.to_dict().get('data', [])})
        else:
            app.logger.info("📁 No data for %s", month)
            return jsonify({'data': []})
    except Exception as e:
        app.logger.error("❌ Load failed: %s", str(e), exc_info=True)
        return jsonify({'error': str(e)}), 500

# === Endpoint to Send Email Alerts for Out-of-Tolerance Values ===
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
