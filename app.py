# --- [UNCHANGED IMPORTS] ---
from flask import Flask, request, jsonify
from flask_cors import CORS
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
import json
import logging
from calendar import monthrange
from datetime import datetime # Import datetime for date parsing
import firebase_admin
from firebase_admin import credentials, firestore, auth

app = Flask(__name__)
CORS(app)
app.logger.setLevel(logging.DEBUG)

# --- [EMAIL CONFIG] ---
SENDER_EMAIL = os.environ.get('SENDER_EMAIL', 'itsmealwin12@gmail.com')
RECEIVER_EMAIL = os.environ.get('RECEIVER_EMAIL', 'alwinjose812@gmail.com') # This is the fallback for admin emails, not QA alerts to RSO
APP_PASSWORD = os.environ.get('EMAIL_APP_PASSWORD')
TEST_EMAIL_RECIPIENT = 'alwinjose812@gmail.com' # Set specific email for health checks

# --- [EMAIL SENDER FUNCTION] ---
def send_notification_email(recipient_email, subject, body):
    if not APP_PASSWORD:
        app.logger.warning(f"🚫 Cannot send notification to {recipient_email}: APP_PASSWORD not configured.")
        return False
    msg = MIMEMultipart()
    msg['From'] = SENDER_EMAIL
    # Handles both single email string and comma-separated string
    msg['To'] = recipient_email
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))
    try:
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        server.login(SENDER_EMAIL, APP_PASSWORD)
        server.send_message(msg)
        server.quit()
        app.logger.info(f"📧 Notification sent to {recipient_email}")
        return True
    except Exception as e:
        app.logger.error(f"❌ Email error: {str(e)}", exc_info=True)
        return False

# --- [FIREBASE INIT] ---
firebase_json = os.environ.get("FIREBASE_CREDENTIALS")
if not firebase_json:
    raise Exception("FIREBASE_CREDENTIALS not set")
firebase_dict = json.loads(firebase_json)
cred = credentials.Certificate(firebase_dict)
firebase_admin.initialize_app(cred)
db = firestore.client()

ENERGY_TYPES = ["6X", "10X", "15X", "6X FFF", "10X FFF", "6E", "9E", "12E", "15E", "18E"]

# --- VERIFY ADMIN TOKEN ---
async def verify_admin_token(id_token):
    try:
        decoded_token = auth.verify_id_token(id_token)
        uid = decoded_token['uid']
        user_doc = db.collection('users').document(uid).get()
        if user_doc.exists and user_doc.to_dict().get('role') == 'Admin':
            return True, uid
    except Exception as e:
        app.logger.error("Token check failed: %s", str(e))
    return False, None

# --- SIGNUP ---
@app.route('/signup', methods=['POST'])
def signup():
    try:
        user = request.get_json(force=True)
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
            'centerId': user['hospital'], # Assuming hospital value is also the centerId
            'status': user['status']
        })
        return jsonify({'status': 'success', 'message': 'User registered'}), 200
    except Exception as e:
        app.logger.error("Signup failed: %s", str(e), exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500

# --- LOGIN ---
@app.route('/login', methods=['POST'])
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
            'hospital': user_data.get("hospital", ""),
            'role': user_data.get("role", ""),
            'uid': uid,
            'centerId': user_data.get("centerId", ""),
            'status': user_data.get("status", "unknown")
        }), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': 'Login failed'}), 500

# --- SAVE DATA ---
@app.route('/save', methods=['POST'])
def save_data():
    try:
        content = request.get_json(force=True)
        uid = content.get("uid")
        month = f"Month_{content.get('month')}"
        raw_data = content.get("data")

        user_doc = db.collection('users').document(uid).get()
        if not user_doc.exists:
            return jsonify({'status': 'error', 'message': 'User not found'}), 404
        user_data = user_doc.to_dict()
        center_id = user_data.get("centerId")
        user_status = user_data.get("status", "pending")

        if user_status != "active":
            return jsonify({'status': 'error', 'message': 'Account not active'}), 403
        if not center_id:
            return jsonify({'status': 'error', 'message': 'Missing centerId'}), 400
        if not isinstance(raw_data, list):
            return jsonify({'status': 'error', 'message': 'Invalid data'}), 400

        converted = [{"row": i, "energy": row[0], "values": row[1:]} for i, row in enumerate(raw_data) if len(row) > 1]
        db.collection("linac_data").document(center_id).collection("months").document(month).set(
            {"data": converted}, merge=True)
        return jsonify({'status': 'success'}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

# --- LOAD DATA ---
@app.route('/data', methods=['GET'])
def get_data():
    try:
        month_param = request.args.get('month')
        uid = request.args.get('uid')
        if not month_param or not uid:
            return jsonify({'error': 'Missing "month" or "uid"'}), 400

        user_doc = db.collection("users").document(uid).get()
        if not user_doc.exists:
            return jsonify({'error': 'User not found'}), 404
        user_data = user_doc.to_dict()
        center_id = user_data.get("centerId")
        user_status = user_data.get("status", "pending")

        if user_status != "active":
            return jsonify({'error': 'Account not active'}), 403
        if not center_id:
            return jsonify({'error': 'Missing centerId'}), 400

        year, mon = map(int, month_param.split("-"))
        _, num_days = monthrange(year, mon)
        energy_dict = {e: [""] * num_days for e in ENERGY_TYPES}

        doc = db.collection("linac_data").document(center_id).collection("months").document(f"Month_{month_param}").get()
        if doc.exists:
            for row in doc.to_dict().get("data", []):
                energy, values = row.get("energy"), row.get("values", [])
                if energy in energy_dict:
                    energy_dict[energy] = values

        table = [[e] + energy_dict[e] for e in ENERGY_TYPES]
        return jsonify({'data': table}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# --- ALERT EMAIL ---
@app.route('/send-alert', methods=['POST'])
async def send_alert():
    month_alerts_doc_ref = None

    try:
        content = request.get_json(force=True)
        current_out_values = content.get("outValues", [])
        hospital = content.get("hospitalName", "Unknown")
        uid = content.get("uid")
        month_key = content.get("month")

        if not uid or not month_key:
            app.logger.warning("Missing UID or month key for alert processing.")
            return jsonify({'status': 'error', 'message': 'Missing UID or month for alert processing'}), 400

        user_doc = db.collection('users').document(uid).get()
        if not user_doc.exists:
            app.logger.warning(f"User document not found for UID: {uid} during alert processing.")
            return jsonify({'status': 'error', 'message': 'User not found for alert processing'}), 404
        user_data = user_doc.to_dict()
        center_id = user_data.get('centerId')

        if not center_id:
            app.logger.warning(f"Center ID not found for user {uid} during alert processing.")
            return jsonify({'status': 'error', 'message': 'Center ID not found for user for alert processing'}), 400

        # NEW: Find all RSO emails for the current hospital/centerId
        rso_emails = []
        try:
            rso_users = db.collection('users').where('centerId', '==', center_id).where('role', '==', 'RSO').stream()
            for rso_user in rso_users:
                rso_data = rso_user.to_dict()
                if 'email' in rso_data and rso_data['email']:
                    rso_emails.append(rso_data['email'])
            
            if not rso_emails:
                app.logger.warning(f"No RSO email found for centerId: {center_id}. Alert not sent to RSO.")
                return jsonify({'status': 'no_rso_email', 'message': 'No RSO email found for this hospital.'}), 200 # Or decide to send to a fallback admin email
        except Exception as e:
            app.logger.error(f"Error fetching RSO emails for center {center_id}: {str(e)}", exc_info=True)
            return jsonify({'status': 'error', 'message': 'Failed to fetch RSO emails'}), 500


        month_alerts_doc_ref = db.collection("linac_alerts").document(center_id).collection("months").document(f"Month_{month_key}")
        app.logger.debug(f"Firestore alerts path: {month_alerts_doc_ref.path}")

        alerts_doc_snap = month_alerts_doc_ref.get()
        previously_alerted = []

        if alerts_doc_snap.exists:
            previously_alerted = alerts_doc_snap.to_dict().get("alerted_values", [])
            app.logger.debug(f"Found {len(previously_alerted)} previously alerted values.")
        else:
            app.logger.debug(f"No existing alert record for {center_id}/{month_key}. This might be the first alert for this month.")

        previously_alerted_strings = set(json.dumps(val, sort_keys=True) for val in previously_alerted)
        current_out_values_strings = set(json.dumps(val, sort_keys=True) for val in current_out_values)

        send_email_needed = False

        if current_out_values_strings != previously_alerted_strings:
            send_email_needed = True
            app.logger.debug("Change in out-of-tolerance values detected. Email will be considered.")
        else:
            app.logger.info("No change in out-of-tolerance values since last alert. Email will not be sent.")
            return jsonify({'status': 'no_change', 'message': 'No new alerts or changes to existing issues. Email not sent.'})

        # --- Construct the email message ---
        message_body = f"LINAC QA Status Update for {hospital} ({month_key})\n\n"

        if current_out_values:
            message_body += "Current Out-of-Tolerance Values (±2.0%) or persisting issues:\n\n"
            sorted_current_out_values = sorted(current_out_values, key=lambda x: (x.get('energy'), x.get('date')))
            for v in sorted_current_out_values:
                formatted_date = v.get('date', 'N/A')
                message_body += f"Energy: {v.get('energy', 'N/A')}, Date: {formatted_date}, Value: {v.get('value', 'N/A')}%\n"
            message_body += "\n"
        elif previously_alerted:
            message_body += "All previously detected LINAC QA issues for this month are now resolved.\n"
        else:
            message_body += "All LINAC QA values are currently within tolerance for this month.\n"
        
        # --- Send the email ---
        msg = MIMEMultipart()
        msg['From'] = SENDER_EMAIL
        msg['To'] = ", ".join(rso_emails) # Join emails with comma for 'To' field
        msg['Subject'] = f"⚠ LINAC QA Status - {hospital} ({month_key})"
        msg.attach(MIMEText(message_body, 'plain'))

        if APP_PASSWORD:
            # Ensure there are recipients before attempting to send
            if rso_emails:
                server = smtplib.SMTP_SSL("smtp.gmail.com", 465)
                server.login(SENDER_EMAIL, APP_PASSWORD)
                # Pass the list of recipients to send_message for proper delivery
                server.send_message(msg)
                server.quit()
                app.logger.info(f"Email alert sent to RSO(s) {msg['To']} for {hospital} ({month_key}).")

                month_alerts_doc_ref.set({"alerted_values": current_out_values}, merge=False)
                app.logger.debug(f"Alert state updated in Firestore for {center_id}/{month_key}.")
            else:
                app.logger.warning(f"No RSO emails collected for {center_id}, skipping email send.")
                return jsonify({'status': 'no_rso_email', 'message': 'No RSO email found for this hospital to send alert to.'}), 200


            return jsonify({'status': 'alert sent', 'message': 'Email sent and alert state updated.'}), 200
        else:
            app.logger.warning("APP_PASSWORD not configured. Cannot send email.")
            return jsonify({'status': 'email not sent', 'message': 'Email credentials missing'}), 500
    except Exception as e:
        app.logger.error(f"Error sending alert: {str(e)}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500

# --- NEW: Chatbot Query Endpoint ---
@app.route('/query-qa-data', methods=['POST'])
def query_qa_data():
    try:
        content = request.get_json(force=True)
        query_type = content.get("query")
        month_param = content.get("month")
        uid = content.get("uid")
        
        # Additional parameters for specific queries
        energy_type = content.get("energy_type")
        date_param = content.get("date") # Expected format:
