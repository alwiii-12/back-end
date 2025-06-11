from flask import Flask, request, jsonify
from flask_cors import CORS
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
import json

# Firebase Admin SDK
import firebase_admin
from firebase_admin import credentials, firestore

app = Flask(__name__)
CORS(app)

# === Configuration for Email Alerts ===
SENDER_EMAIL = 'itsmealwin12@gmail.com'
RECEIVER_EMAIL = 'alwinjose812@gmail.com'
APP_PASSWORD = 'tjvy ksue rpnk xmaf'

# === Firebase Firestore Initialization ===
# Load Firebase credentials from environment variable. Make sure you've set this in Render.
firebase_json = os.environ.get("FIREBASE_CREDENTIALS")
if not firebase_json:
    raise Exception("FIREBASE_CREDENTIALS environment variable not set.")

firebase_dict = json.loads(firebase_json)
cred = credentials.Certificate(firebase_dict)
firebase_admin.initialize_app(cred)
db = firestore.client()

# === Endpoint to Save QA Data into Firestore ===
@app.route('/save', methods=['POST'])
def save_data():
    """
    Expects JSON payload with keys:
      - "month": string in format "YYYY-MM"
      - "data": the table data (list of lists)
    The data is stored in Firestore under collection "linac_data" with document id = month.
    """
    content = request.get_json()
    try:
        month = content['month']
        data = content['data']
    except KeyError:
        return jsonify({'status': 'error', 'message': 'Missing month or data'}), 400

    try:
        # Save data under document with ID equal to the month
        db.collection('linac_data').document(month).set({'data': data})
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

# === Endpoint to Load QA Data from Firestore ===
@app.route('/data', methods=['GET'])
def get_data():
    """
    Expects query parameter "month" (format "YYYY-MM").
    Returns the stored data from Firestore or an empty list if not found.
    """
    month = request.args.get('month')
    if not month:
        return jsonify({'error': 'Missing month parameter'}), 400

    try:
        doc_ref = db.collection('linac_data').document(month)
        doc = doc_ref.get()
        if doc.exists:
            return jsonify({'data': doc.to_dict().get('data', [])})
        else:
            return jsonify({'data': []})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# === Endpoint to Send Email Alerts for Out-of-Tolerance Values ===
@app.route('/send-alert', methods=['POST'])
def send_alert():
    """
    Expects JSON payload with key:
      - "outValues": list of objects { energy, date, value }
    Sends an email alert to the designated receiver.
    """
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

# === Main Entry Point ===
if __name__ == '__main__':
    app.run(debug=True)
