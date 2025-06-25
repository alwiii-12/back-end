from flask import Flask, request, jsonify
from flask_cors import CORS
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart # CONFIRMED: Correct import for MIMEMultipart
import os
import json
import logging
from calendar import monthrange

# Firebase Admin SDK
import firebase_admin
from firebase_admin import credentials, firestore, auth


app = Flask(__name__)
CORS(app, origins=["https://front-endnew.onrender.com"])
app.logger.setLevel(logging.DEBUG)

# === Email Config ===
SENDER_EMAIL = os.environ.get('SENDER_EMAIL', 'itsmealwin12@gmail.com')
RECEIVER_EMAIL = os.environ.get('RECEIVER_EMAIL', 'alwinjose812@gmail.com') # This is for alerts, not notifications
APP_PASSWORD = os.environ.get('EMAIL_APP_PASSWORD')
if not APP_PASSWORD:
    app.logger.error("🔥 EMAIL_APP_PASSWORD environment variable not set.")


# --- Helper function to send notification emails ---
def send_notification_email(recipient_email, subject, body):
    if not APP_PASSWORD:
        app.logger.warning(f"🚫 Cannot send notification to {recipient_email}: APP_PASSWORD not configured.")
        return False

    msg = MIMEMultipart()
    msg['From'] = SENDER_EMAIL
    msg['To'] = recipient_email
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))

    try:
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        server.login(SENDER_EMAIL, APP_PASSWORD)
        server.send_message(msg)
        server.quit()
        app.logger.info(f"📧 Notification email sent to {recipient_email} successfully.")
        return True
    except Exception as e:
        app.logger.error(f"❌ Failed to send notification email to {recipient_email}: {str(e)}", exc_info=True)
        return False


# === Firebase Init ===
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

ENERGY_TYPES = ["6X", "10X", "15X", "6X FFF", "10X FFF", "6E", "9E", "12E", "15E", "18E"]

# Helper function to verify Firebase ID token and get user role/UID
async def verify_admin_token(id_token):
    try:
        decoded_token = auth.verify_id_token(id_token)
        uid = decoded_token['uid']
        user_doc = db.collection('users').document(uid).get()
        if user_doc.exists:
            user_data = user_doc.to_dict()
            if user_data.get('role') == 'Admin':
                return True, uid
        return False, None
    except Exception as e:
        app.logger.error("Token verification failed: %s", str(e))
        return False, None

# === Signup ===
@app.route('/signup', methods=['POST'])
@app.route('/signup/', methods=['POST'])
def signup():
    try:
        user = request.get_json(force=True)
        app.logger.info("🆕 Signup request: %s", user)

        required = ['name', 'email', 'hospital', 'role', 'uid', 'status']
        missing = [f for f in required if f not in user or user[f].strip() == ""]
        if missing:
            return jsonify({'status': 'error', 'message': f'Missing fields: {", ".join(missing)}'}), 400

        user_ref = db.collection('users').document(user['uid'])
        if user_ref.get().exists:
            return jsonify({'status': 'error', 'message': 'User already exists'}), 409

        user_ref.set({
            'name': user['name'],
            'email': user['email'].strip().lower(),
            'hospital': user['hospital'],
            'role': user['role'],
            'centerId': user['hospital'],
            'status': user['status']
        })

        return jsonify({'status': 'success', 'message': 'User registered successfully'}), 200
    except Exception as e:
        app.logger.error("❌ Signup failed: %s", str(e), exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500

# === Login (UID-Based) ===
@app.route('/login', methods=['POST'])
@app.route('/login/', methods=['POST'])
def login():
    try:
        content = request.get_json(force=True)
        uid = content.get("uid", "").strip()

        if not uid:
            return jsonify({'status': 'error', 'message': 'Missing UID'}), 400

        user_ref = db.collection("users").document(uid)
        user_doc = user_ref.get()

        if not user_doc.exists:
            return jsonify({'status': 'error', 'message': 'User not found'}), 404

        user_data = user_doc.to_dict()

        return jsonify({
            'status': 'success',
            'message': 'Login successful',
            'hospital': user_data.get("hospital", ""),
            'role': user_data.get("role", ""),
            'uid': uid,
            'centerId': user_data.get("centerId", ""),
            'status': user_data.get("status", "unknown")
        }), 200

    except Exception as e:
        app.logger.error("❌ Login error: %s", str(e), exc_info=True)
        return jsonify({'status': 'error', 'message': 'Login failed'}), 500

# === Save Monthly QA Data ===
@app.route('/save', methods=['POST'])
@app.route('/save/', methods=['POST'])
def save_data():
    try:
        content = request.get_json(force=True)
        app.logger.info("📥 Save request: %s", content)

        if 'month' not in content or 'data' not in content or 'uid' not in content:
            return jsonify({'status': 'error', 'message': f'Missing "month", "uid", or "data"'}), 400

        uid = content['uid']
        month = f"Month_{content['month']}"
        raw_data = content['data']

        user_doc = db.collection('users').document(uid).get()
        if not user_doc.exists:
            return jsonify({'status': 'error', 'message': 'User not found for saving data'}), 404
        user_data = user_doc.to_dict()
        center_id = user_data.get('centerId')
        user_status = user_data.get('status')
        
        if user_status != 'active':
            return jsonify({'status': 'error', 'message': 'Account not active. Awaiting admin approval.'}), 403

        if not center_id:
            return jsonify({'status': 'error', 'message': 'User not linked to a center'}), 400

        if not isinstance(raw_data, list):
            return jsonify({'status': 'error', 'message': 'Data must be a 2D array'}), 400

        converted_data = []
        for i, row in enumerate(raw_data):
            if len(row) > 1:
                converted_data.append({
                    'row': i,
                    'energy': row[0],
                    'values': row[1:]
                })

        db.collection('linac_data').document(center_id).collection('months').document(month).set(
            {
                'data': converted_data,
            },
            merge=True
        )

        app.logger.info("✅ Data saved for %s/%s", center_id, month)
        return jsonify({'status': 'success'}), 200

    except Exception as e:
        app.logger.error("❌ Save failed: %s", str(e), exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500

# === Load Monthly QA Data ===
@app.route('/data', methods=['GET'])
@app.route('/data/', methods=['GET'])
def get_data():
    month_param = request.args.get('month')
    uid = request.args.get('uid')

    if not month_param or not uid:
        return jsonify({'error': 'Missing "month" or "uid" parameter'}), 400

    user_doc = db.collection('users').document(uid).get()
    if not user_doc.exists:
        return jsonify({'error': 'User not found for loading data'}), 404
    user_data = user_doc.to_dict()
    center_id = user_data.get('centerId')
    user_status = user_data.get('status')

    if user_status != 'active':
        return jsonify({'error': 'Account not active. Awaiting admin approval.'}), 403

    if not center_id:
        return jsonify({'error': 'User not linked to a center for data loading'}), 400

    doc_id = f"Month_{month_param}"
    try:
        year, mon = map(int, month_param.split("-"))
        _, num_days = monthrange(year, mon)

        energy_dict = {energy: [""] * num_days for energy in ENERGY_TYPES}

        doc = db.collection('linac_data').document(center_id).collection('months').document(doc_id).get()
        if doc.exists:
            data_from_db = doc.to_dict()
            
            # Extract main QA data
            data = data_from_db.get('data', [])

            for row in data:
                energy = row.get('energy', '')
                values = row.get('values', [])
                if energy in energy_dict:
                    energy_dict[energy] = values

            table = [[energy] + energy_dict[energy] for energy in ENERGY_TYPES]
            
            return jsonify({'data': table}), 200

    except Exception as e:
        app.logger.error("❌ Load failed: %s", str(e), exc_info=True)
        return jsonify({'error': str(e)}), 500

# === Send Alert Email ===
@app.route('/send-alert', methods=['POST'])
@app.route('/send-alert/', methods=['POST'])
def send_alert():
    try:
        content = request.get_json(force=True)
        out_values = content.get('outValues', [])
        hospital_name = content.get('hospitalName', 'Unknown Hospital')

        if not out_values:
            return jsonify({'status': 'no alerts sent'})

        message_body = f"""
Dear LINAC QA User,

This is an automated alert from the LINAC QA System.

Please review the following LINAC QA output values that are out of tolerance (±2.0%):

--- Out of Tolerance Values ---
"""
        for val in out_values:
            message_body += f"- Energy: {val['energy']}\n"
            message_body += f"  Date: {val['date']}\n"
            message_body += f"  Value: {val['value']}%\n"
            message_body += f"-----------------------------\n"

        message_body += f"""
Alert from Hospital: {hospital_name}

Please log into the LINAC QA Portal for more details and to take necessary action.
Login Page: https://front-endnew.onrender.com/login.html
""" # Removed /* */ comments directly inside f-string

        msg = MIMEMultipart()
        msg['From'] = SENDER_EMAIL
        msg['To'] = RECEIVER_EMAIL
        msg['Subject'] = f'⚠ LINAC QA Output Failed Alert - {hospital_name}'
        msg.attach(MIMEText(message_body, 'plain'))

        if APP_PASSWORD:
            server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
            server.login(SENDER_EMAIL, APP_PASSWORD)
            server.send_message(msg)
            app.logger.info("📧 Alert email sent successfully.")
            return jsonify({'status': 'alert sent'}), 200
        else:
            app.logger.warning(f"🚫 Email not sent: APP_PASSWORD environment variable not configured.")
            return jsonify({'status': 'email not sent', 'message': 'APP_PASSWORD not configured'}), 500

    except Exception as e:
        app.logger.error("❌ Email error: %s", str(e), exc_info=True)
        return jsonify({'message': 'Internal Server Error'}), 500


@app.route('/')
def index():
    return "✅ LINAC QA Backend Running"

if __name__ == '__main__':
    # NEW: Debugging: Log all registered routes on startup
    with app.app_context(): # Ensure we are within an app context
        app.logger.info("--- Registered Flask Routes ---")
        for rule in app.url_map.iter_rules():
            # Filter out internal/debug routes if desired
            if 'static' not in rule.endpoint: # Exclude static files
                 app.logger.info(f"Endpoint: {rule.endpoint}, Methods: {rule.methods}, Rule: {rule.rule}")
        app.logger.info("-----------------------------")

    app.run(debug=True)
