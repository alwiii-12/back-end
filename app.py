# --- [SENTRY INTEGRATION - NEW IMPORTS AND INITIALIZATION] ---
import sentry_sdk
from sentry_sdk.integrations.flask import FlaskIntegration
import os

# Retrieve Sentry DSN from environment variable
SENTRY_DSN = os.environ.get("SENTRY_DSN")

sentry_sdk_configured = False # Flag to track Sentry initialization
if SENTRY_DSN:
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[
            FlaskIntegration(),
        ],
        traces_sample_rate=1.0, # Capture 100% of transactions for performance monitoring
        profiles_sample_rate=1.0, # Capture 100% of active samples for profiling
        send_default_pii=True # Enable sending of PII (Personally Identifiable Information)
    )
    sentry_sdk_configured = True
    print("Sentry initialized successfully.")
else:
    print("SENTRY_DSN environment variable not set. Sentry not initialized.")


# --- [UNCHANGED IMPORTS] ---
from flask import Flask, request, jsonify, send_file, abort
from flask_cors import CORS
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import json
import logging
from calendar import monthrange
from datetime import datetime, timedelta
import re 
import pytz # Import pytz for timezone handling

import firebase_admin
from firebase_admin import credentials, firestore, auth, app_check
import jwt

# New imports for Excel export
import pandas as pd
from io import BytesIO

import numpy as np 


# Set nlp to None explicitly, as it's no longer loaded
nlp = None # This makes sure the 'nlp is None' check always passes


app = Flask(__name__)

# --- [CORS CONFIGURATION] ---
origins = [
    "https://front-endnew.onrender.com"
]
CORS(app, resources={r"/*": {"origins": origins}})

app.logger.setLevel(logging.DEBUG)

# --- [EMAIL CONFIG] ---
SENDER_EMAIL = os.environ.get('SENDER_EMAIL', 'itsmealwin12@gmail.com')
RECEIVER_EMAIL = os.environ.get('RECEIVER_EMAIL', 'alwinjose812@gmail.com')
APP_PASSWORD = os.environ.get('EMAIL_APP_PASSWORD')

# --- [EMAIL SENDER FUNCTION] ---
def send_notification_email(recipient_email, subject, body):
    if not APP_PASSWORD:
        app.logger.warning(f"🚫 Cannot send notification to {recipient_email}: APP_PASSWORD not configured.")
        if sentry_sdk_configured:
            sentry_sdk.capture_message(f"EMAIL_APP_PASSWORD not set. Cannot send notification to {recipient_email}.", level="warning")
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
        app.logger.info(f"📧 Notification sent to {recipient_email}")
        return True
    except Exception as e:
        app.logger.error(f"❌ Email error: {str(e)} for recipient {recipient_email}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e) # Capture the exception with Sentry
        return False

# --- [FIREBASE INIT] ---
firebase_json = os.environ.get("FIREBASE_CREDENTIALS")
if not firebase_json:
    if sentry_sdk_configured:
        sentry_sdk.capture_message("CRITICAL: FIREBASE_CREDENTIALS environment variable not set.", level="fatal")
    raise Exception("FIREBASE_CREDENTIALS not set")
firebase_dict = json.loads(firebase_json)

if not firebase_admin._apps:
    cred = credentials.Certificate(firebase_dict)
    firebase_admin.initialize_app(cred)
    app.logger.info("Firebase default app initialized.")
else:
    app.logger.info("Firebase default app already initialized, skipping init.")

db = firestore.client()

# --- APP CHECK VERIFICATION ---
@app.before_request
def verify_app_check_token():
    app_check_token = request.headers.get('X-Firebase-AppCheck')
    if request.method == 'OPTIONS':
        return None
    if request.path == '/':
        return None
    if not app_check_token:
        app.logger.warning("App Check token missing.")
        return jsonify({'error': 'Unauthorized: App Check token is missing'}), 401
    try:
        app_check.verify_token(app_check_token)
        return None
    except (ValueError, jwt.exceptions.DecodeError) as e:
        app.logger.error(f"Invalid App Check token: {e}")
        return jsonify({'error': f'Unauthorized: Invalid App Check token'}), 401
    except Exception as e:
        app.logger.error(f"App Check verification failed with an unexpected error: {e}")
        return jsonify({'error': 'Unauthorized: App Check verification failed'}), 401

# --- CONSTANTS ---
ENERGY_TYPES = ["6X", "10X", "15X", "6X FFF", "10X FFF", "6E", "9E", "12E", "15E", "18E"]
DATA_TYPES = ["output", "flatness", "inline", "crossline"]
DATA_TYPE_CONFIGS = {
    "output": {"warning": 1.8, "tolerance": 2.0},
    "flatness": {"warning": 0.9, "tolerance": 1.0},
    "inline": {"warning": 0.9, "tolerance": 1.0},
    "crossline": {"warning": 0.9, "tolerance": 1.0}
}

# --- VERIFY ADMIN TOKEN ---
def verify_admin_token(id_token):
    try:
        decoded_token = auth.verify_id_token(id_token)
        uid = decoded_token['uid']
        user_doc = db.collection('users').document(uid).get()
        if user_doc.exists and user_doc.to_dict().get('role') == 'Admin':
            return True, uid
    except Exception as e:
        app.logger.error(f"Token verification failed: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e)
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
            'centerId': user['hospital'],
            'status': user['status']
        })
        return jsonify({'status': 'success', 'message': 'User registered'}), 200
    except Exception as e:
        app.logger.error(f"Signup failed: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e)
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
            return jsonify({'status': 'error', 'message': 'User profile not found in database.'}), 404
        
        user_data = user_doc.to_dict()
        user_status = user_data.get("status", "unknown")

        if user_status == "pending":
            return jsonify({'status': 'error', 'message': 'Your account is awaiting administrator approval.'}), 403
        
        if user_status == "rejected":
            return jsonify({'status': 'error', 'message': 'Your account has been rejected. Please contact support.'}), 403
            
        if user_status != "active":
            return jsonify({'status': 'error', 'message': 'This account is not active.'}), 403

        return jsonify({
            'status': 'success',
            'hospital': user_data.get("hospital", ""),
            'role': user_data.get("role", ""),
            'uid': uid,
            'centerId': user_data.get("centerId", ""),
            'status': user_status
        }), 200

    except Exception as e:
        app.logger.error(f"Login failed: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e)
        return jsonify({'status': 'error', 'message': 'An internal server error occurred during login.'}), 500

# --- UPDATE PROFILE ROUTE ---
@app.route('/update-profile', methods=['POST'])
def update_profile():
    try:
        content = request.get_json(force=True)
        uid = content.get("uid")
        new_name = content.get("name")
        new_hospital = content.get("hospital")

        if not all([uid, new_name, new_hospital]):
            return jsonify({'status': 'error', 'message': 'Missing required fields'}), 400

        user_ref = db.collection('users').document(uid)
        if not user_ref.get().exists:
            return jsonify({'status': 'error', 'message': 'User not found'}), 404

        updates = {
            'name': new_name,
            'hospital': new_hospital,
            'centerId': new_hospital # Also update centerId if it's tied to hospital
        }
        
        user_ref.update(updates)

        # Log the update action
        audit_entry = {
            "timestamp": firestore.SERVER_TIMESTAMP,
            "userUid": uid,
            "action": "profile_self_update",
            "changes": {
                "name": new_name,
                "hospital": new_hospital
            }
        }
        db.collection("audit_logs").add(audit_entry)
        
        app.logger.info(f"User {uid} updated their profile.")
        return jsonify({'status': 'success', 'message': 'Profile updated successfully'}), 200

    except Exception as e:
        app.logger.error(f"Profile update failed: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e)
        return jsonify({'status': 'error', 'message': str(e)}), 500

# --- LOG EVENT ---
@app.route('/log_event', methods=['POST', 'OPTIONS'])
def log_event():
    if request.method == 'OPTIONS':
        return '', 200

    try:
        event_data = request.get_json(force=True)
        
        if not event_data.get("action") or not event_data.get("userUid"):
            app.logger.warning("Attempted to log event with missing action or userUid.")
            return jsonify({'status': 'error', 'message': 'Missing action or userUid'}), 400

        if 'hospital' in event_data and event_data['hospital']:
            event_data['hospital'] = event_data['hospital'].lower().replace(" ", "_")

        event_data["timestamp"] = firestore.SERVER_TIMESTAMP
        
        db.collection("audit_logs").add(event_data)
        app.logger.info(f"Audit: Logged event '{event_data.get('action')}' for UID {event_data.get('userUid')}.")
        return jsonify({'status': 'success', 'message': 'Event logged successfully'}), 200
    except Exception as e:
        app.logger.error(f"Error logging event: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e)
        return jsonify({'status': 'error', 'message': str(e)}), 500

# --- SAVE DATA ---
@app.route('/save', methods=['POST'])
def save_data():
    try:
        content = request.get_json(force=True)
        uid = content.get("uid")
        month_param = content.get("month")
        raw_data = content.get("data")
        data_type = content.get("dataType")

        if not data_type or data_type not in DATA_TYPES:
            return jsonify({'status': 'error', 'message': 'Invalid or missing dataType'}), 400

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

        month_doc_id = f"Month_{month_param}"
        converted = [{"row": i, "energy": row[0], "values": row[1:]} for i, row in enumerate(raw_data) if len(row) > 1]
        
        firestore_field_name = f"data_{data_type}"
        doc_ref = db.collection("linac_data").document(center_id).collection("months").document(month_doc_id)
        doc_ref.set({firestore_field_name: converted}, merge=True)
        
        return jsonify({'status': 'success', 'message': f'{data_type} data saved successfully'}), 200

    except Exception as e:
        app.logger.error(f"Save data failed for {data_type}: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e)
        return jsonify({'status': 'error', 'message': str(e)}), 500


# --- LOAD DATA ---
@app.route('/data', methods=['GET'])
def get_data():
    try:
        month_param = request.args.get('month')
        uid = request.args.get('uid')
        data_type = request.args.get('dataType')

        if not month_param or not uid:
            return jsonify({'error': 'Missing "month" or "uid"'}), 400
        if not data_type or data_type not in DATA_TYPES:
            return jsonify({'error': 'Invalid or missing dataType'}), 400

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
        
        firestore_field_name = f"data_{data_type}"

        doc = db.collection("linac_data").document(center_id).collection("months").document(f"Month_{month_param}").get()
        if doc.exists:
            doc_data = doc.to_dict().get(firestore_field_name, [])
            for row in doc_data:
                energy, values = row.get("energy"), row.get("values", [])
                if energy in energy_dict:
                    energy_dict[energy] = (values + [""] * num_days)[:num_days]

        table = [[e] + energy_dict[e] for e in ENERGY_TYPES]
        return jsonify({'data': table}), 200
    except Exception as e:
        app.logger.error(f"Get data failed for {data_type}: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500


# --- ALERT EMAIL (with added logging) ---
@app.route('/send-alert', methods=['POST'])
def send_alert():
    app.logger.info("--- 📧 /send-alert endpoint triggered ---")
    try:
        content = request.get_json(force=True)
        uid = content.get("uid")
        app.logger.info(f"Step 1: Received alert request from UID: {uid}")

        user_doc = db.collection('users').document(uid).get()
        if not user_doc.exists:
            app.logger.warning("Step 2 FAILED: User document not found.")
            return jsonify({'status': 'error', 'message': 'User not found'}), 404
        
        user_data = user_doc.to_dict()
        center_id = user_data.get('centerId')
        app.logger.info(f"Step 2: Found user's centerId: '{center_id}'")

        if not center_id:
             app.logger.warning("Step 3 FAILED: centerId is missing for the user.")
             return jsonify({'status': 'error', 'message': 'Center ID not found for user'}), 400

        app.logger.info(f"Step 3: Querying for RSO with centerId='{center_id}' and role='RSO'")
        rso_users_query = db.collection('users').where('centerId', '==', center_id).where('role', '==', 'RSO')
        rso_users_stream = rso_users_query.stream()
        
        rso_emails = [rso.to_dict()['email'] for rso in rso_users_stream if 'email' in rso.to_dict()]
        
        app.logger.info(f"Step 4: Found {len(rso_emails)} RSO emails: {rso_emails}")

        if not rso_emails:
            app.logger.warning("Step 5: No RSO emails found. Alert process stopping here.")
            return jsonify({'status': 'no_rso_email', 'message': 'No RSO email found for this hospital.'}), 200

        app.logger.info("Step 5: RSO emails found. Proceeding to send notification.")
        
        current_out_values = content.get("outValues", [])
        hospital = content.get("hospitalName", "Unknown")
        month_key = content.get("month")
        data_type = content.get("dataType", "output")
        tolerance_percent = content.get("tolerance", 2.0)
        data_type_display = data_type.replace("_", " ").title()

        month_alerts_doc_ref = db.collection("linac_alerts").document(center_id).collection("months").document(f"Month_{month_key}_{data_type}")
        alerts_doc_snap = month_alerts_doc_ref.get()
        
        previously_alerted = alerts_doc_snap.to_dict().get("alerted_values", []) if alerts_doc_snap.exists else []
        previously_alerted_strings = set(json.dumps(val, sort_keys=True) for val in previously_alerted)
        current_out_values_strings = set(json.dumps(val, sort_keys=True) for val in current_out_values)

        if current_out_values_strings == previously_alerted_strings:
            app.logger.info("Step 6: No changes in alert values. Email not sent.")
            return jsonify({'status': 'no_change', 'message': 'No new alerts or changes. Email not sent.'})

        message_body = f"{data_type_display} QA Status Update for {hospital} ({month_key})\n\n"
        if current_out_values:
            message_body += f"Current Out-of-Tolerance Values (±{tolerance_percent}%):\n\n"
            for v in sorted(current_out_values, key=lambda x: (x.get('energy'), x.get('date'))):
                message_body += f"Energy: {v.get('energy', 'N/A')}, Date: {v.get('date', 'N/A')}, Value: {v.get('value', 'N/A')}%\n"
        else:
            message_body += f"All previously detected {data_type_display} QA issues for this month are now resolved.\n"

        app.logger.info(f"Step 6: Attempting to send email to: {', '.join(rso_emails)}")
        email_sent = send_notification_email(", ".join(rso_emails), f"⚠ {data_type_display} QA Status - {hospital} ({month_key})", message_body)

        if email_sent:
            app.logger.info("Step 7: Email sent successfully.")
            month_alerts_doc_ref.set({"alerted_values": current_out_values}, merge=False)
            return jsonify({'status': 'alert sent'}), 200
        else:
            app.logger.error("Step 7 FAILED: The send_notification_email function returned False.")
            return jsonify({'status': 'email_send_error', 'message': 'Failed to send email.'}), 500

    except Exception as e:
        app.logger.error(f"--- ❌ UNHANDLED EXCEPTION in /send-alert: {str(e)} ---", exc_info=True)
        if SENTRY_DSN: sentry_sdk.capture_exception(e)
        return jsonify({'status': 'error', 'message': str(e)}), 500


# --- [UPDATED] CHATBOT / DIAGNOSTICS ---
@app.route('/query-qa-data', methods=['POST'])
def query_qa_data():
    try:
        content = request.get_json(force=True)
        user_query_text = content.get("query_text", "").lower()

        with open('knowledge_base.json', 'r') as f:
            kb = json.load(f)

        # Check for diagnostic keywords
        if 'drift' in user_query_text or 'output' in user_query_text:
            topic = 'output_drift'
        elif 'flatness' in user_query_text or 'symmetry' in user_query_text:
            topic = 'flatness_warning'
        else:
            # Fallback for simple maintenance questions
            for keyword, path in kb.get("maintenance_info", {}).items():
                 if keyword.replace("_", " ") in user_query_text:
                     return jsonify({'status': 'success', 'message': path}), 200
            return jsonify({'status': 'error', 'message': "I can help diagnose issues with 'output drift' or 'flatness'. What would you like to diagnose?"}), 404

        # Start the diagnostic flow
        troubleshooting_flow = kb.get("troubleshooting", {}).get(topic)
        if not troubleshooting_flow:
            return jsonify({'status': 'error', 'message': "I can't find a diagnostic flow for that topic."}), 404

        start_node_id = troubleshooting_flow.get('start_node')
        start_node = troubleshooting_flow.get('nodes', {}).get(start_node_id)

        if not start_node:
            return jsonify({'status': 'error', 'message': "Could not start the diagnostic flow."}), 500

        return jsonify({
            'status': 'diagnostic_start',
            'topic': topic,
            'node_id': start_node_id,
            'question': start_node.get('question'),
            'options': start_node.get('options', [])
        }), 200

    except Exception as e:
        app.logger.error(f"Chatbot query failed: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e)
        return jsonify({'status': 'error', 'message': str(e)}), 500

# --- [NEW] DIAGNOSE STEP ENDPOINT ---
@app.route('/diagnose-step', methods=['POST'])
def diagnose_step():
    try:
        content = request.get_json(force=True)
        topic = content.get("topic")
        current_node_id = content.get("node_id")
        answer = content.get("answer")

        if not all([topic, current_node_id, answer]):
            return jsonify({'status': 'error', 'message': 'Missing topic, node_id, or answer'}), 400

        with open('knowledge_base.json', 'r') as f:
            kb = json.load(f)

        flow = kb.get("troubleshooting", {}).get(topic)
        if not flow:
            return jsonify({'status': 'error', 'message': 'Invalid topic'}), 404

        current_node = flow.get("nodes", {}).get(current_node_id)
        if not current_node:
            return jsonify({'status': 'error', 'message': 'Invalid node ID'}), 404
        
        next_node_id = current_node.get("answers", {}).get(answer)
        if not next_node_id:
            return jsonify({'status': 'error', 'message': 'Invalid answer for this node'}), 404
            
        next_node = flow.get("nodes", {}).get(next_node_id)
        if not next_node:
            return jsonify({'status': 'error', 'message': 'Next node not found in knowledge base'}), 500

        # Check if this is a final diagnosis or another question
        if "diagnosis" in next_node:
            return jsonify({
                'status': 'diagnostic_end',
                'diagnosis': next_node.get('diagnosis')
            }), 200
        elif "question" in next_node:
            return jsonify({
                'status': 'diagnostic_continue',
                'topic': topic,
                'node_id': next_node_id,
                'question': next_node.get('question'),
                'options': next_node.get('options', [])
            }), 200
        else:
            return jsonify({'status': 'error', 'message': 'Could not determine next step.'}), 500

    except Exception as e:
        app.logger.error(f"Diagnose step failed: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e)
        return jsonify({'status': 'error', 'message': str(e)}), 500

# --- NEW PREDICTIONS ENDPOINT ---
@app.route('/predictions', methods=['GET'])
def get_predictions():
    try:
        uid = request.args.get('uid')
        data_type = request.args.get('dataType')
        energy = request.args.get('energy')

        if not all([uid, data_type, energy]):
            return jsonify({'error': 'Missing required parameters'}), 400

        user_doc = db.collection("users").document(uid).get()
        if not user_doc.exists:
            return jsonify({'error': 'User not found'}), 404
        center_id = user_doc.to_dict().get("centerId")

        if not center_id:
            return jsonify({'error': 'User has no associated center'}), 400

        prediction_doc_id = f"{center_id}_{data_type}_{energy}"
        prediction_doc = db.collection("linac_predictions").document(prediction_doc_id).get()

        if prediction_doc.exists:
            return jsonify(prediction_doc.to_dict()), 200
        else:
            return jsonify({'error': 'Prediction not found'}), 404
            
    except Exception as e:
        app.logger.error(f"Get predictions failed: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500

# --- ADMIN ROUTES ---
@app.route('/admin/pending-users', methods=['GET'])
def get_pending_users():
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_admin, _ = verify_admin_token(token)
    if not is_admin:
        return jsonify({'message': 'Unauthorized'}), 403
    try:
        users = db.collection("users").where('status', '==', "pending").stream()
        return jsonify([doc.to_dict() | {"uid": doc.id} for doc in users]), 200
    except Exception as e:
        app.logger.error(f"Get pending users failed: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e)
        return jsonify({'message': str(e)}), 500

@app.route('/admin/users', methods=['GET'])
def get_all_users():
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_admin, _ = verify_admin_token(token)
    if not is_admin:
        return jsonify({'message': 'Unauthorized'}), 403

    status_filter = request.args.get('status')
    hospital_filter = request.args.get('hospital')
    search_term = request.args.get('search')

    try:
        users_query = db.collection("users")

        if status_filter:
            users_query = users_query.where('status', '==', status_filter)
        
        if hospital_filter:
            users_query = users_query.where('hospital', '==', hospital_filter)

        users_stream = users_query.stream()
        
        all_users = []
        for doc in users_stream:
            user_data = doc.to_dict()
            user_data['uid'] = doc.id

            if search_term:
                search_term_lower = search_term.lower()
                if not (search_term_lower in user_data.get('name', '').lower() or
                        search_term_lower in user_data.get('email', '').lower() or
                        search_term_lower in user_data.get('role', '').lower() or
                        search_term_lower in user_data.get('hospital', '').lower()):
                    continue
            
            all_users.append(user_data)

        return jsonify(all_users), 200
    except Exception as e:
        app.logger.error(f"Error loading all users: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e)
        return jsonify({'message': str(e)}), 500

@app.route('/admin/update-user-status', methods=['POST'])
def update_user_status():
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_admin, admin_uid_from_token = verify_admin_token(token)
    if not is_admin:
        return jsonify({'message': 'Unauthorized'}), 403
    try:
        content = request.get_json(force=True)
        uid = content.get("uid")
        
        requesting_admin_uid = content.get("admin_uid", admin_uid_from_token) 

        new_status = content.get("status")
        new_role = content.get("role")
        new_hospital = content.get("hospital")

        if not uid:
            return jsonify({'message': 'UID is required'}), 400
        
        updates = {}
        if new_status is not None and new_status in ["active", "pending", "rejected"]:
            updates["status"] = new_status
        if new_role is not None and new_role in ["Medical physicist", "RSO", "Admin"]:
            updates["role"] = new_role
        if new_hospital is not None and new_hospital.strip() != "":
            updates["hospital"] = new_hospital
            updates["centerId"] = new_hospital

        if not updates:
            return jsonify({'message': 'No valid fields provided for update'}), 400

        ref = db.collection("users").document(uid)
        
        old_user_doc = ref.get()
        old_user_data = old_user_doc.to_dict() if old_user_doc.exists else {}

        ref.update(updates)

        audit_entry = {
            "timestamp": firestore.SERVER_TIMESTAMP,
            "adminUid": requesting_admin_uid,
            "action": "user_update",
            "targetUserUid": uid,
            "changes": {},
            "oldData": {},
            "newData": {},
            "hospital": old_user_data.get("hospital", "N/A").lower().replace(" ", "_")
        }

        if "status" in updates:
            audit_entry["changes"]["status"] = {"old": old_user_data.get("status"), "new": updates["status"]}
        if "role" in updates:
            audit_entry["changes"]["role"] = {"old": old_user_data.get("role"), "new": updates["role"]}
        if "hospital" in updates:
            audit_entry["changes"]["hospital"] = {"old": old_user_data.get("hospital"), "new": updates["hospital"]}
        
        db.collection("audit_logs").add(audit_entry)
        app.logger.info(f"Audit: User {uid} updated by {requesting_admin_uid}")

        updated_user_data = ref.get().to_dict()
        if updated_user_data.get("email"):
            subject = "LINAC QA Account Update"
            body = "Your LINAC QA account details have been updated."
            if "status" in updates:
                body += f"\nYour account status is now: {updates['status'].upper()}."
            if "role" in updates:
                 body += f"\nYour role has been updated to: {updates['role']}."
            if "hospital" in updates:
                 body += f"\nYour hospital has been updated to: {updates['hospital']}."
            send_notification_email(updated_user_data["email"], subject, body)

        return jsonify({'status': 'success', 'message': 'User updated successfully'}), 200
    except Exception as e:
        app.logger.error(f"Error updating user: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e)
        return jsonify({'message': str(e)}), 500

@app.route('/admin/delete-user', methods=['DELETE'])
def delete_user():
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_admin, admin_uid_from_token = verify_admin_token(token)
    if not is_admin:
        return jsonify({'message': 'Unauthorized'}), 403

    try:
        content = request.get_json(force=True)
        uid_to_delete = content.get("uid")
        requesting_admin_uid = content.get("admin_uid", admin_uid_from_token)

        if not uid_to_delete:
            return jsonify({'message': 'Missing UID for deletion'}), 400

        user_doc_ref = db.collection("users").document(uid_to_delete)
        user_data_to_log = user_doc_ref.get().to_dict() or {}

        try:
            auth.delete_user(uid_to_delete)
        except Exception as e:
            if "User record not found" not in str(e):
                app.logger.error(f"Error deleting Firebase Auth user {uid_to_delete}: {str(e)}", exc_info=True)
                return jsonify({'message': f"Failed to delete Firebase Auth user: {str(e)}"}), 500

        user_doc_ref.delete()
        
        audit_entry = {
            "timestamp": firestore.SERVER_TIMESTAMP,
            "adminUid": requesting_admin_uid,
            "action": "user_deletion",
            "targetUserUid": uid_to_delete,
            "deletedUserData": user_data_to_log
        }
        db.collection("audit_logs").add(audit_entry)
        app.logger.info(f"Audit: User {uid_to_delete} deleted by {requesting_admin_uid}")

        return jsonify({'status': 'success', 'message': 'User deleted successfully'}), 200

    except Exception as e:
        app.logger.error(f"Error deleting user: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e)
        return jsonify({'message': f"Failed to delete user: {str(e)}"}), 500

# --- ADMIN ROUTE FOR HOSPITAL DATA ---
@app.route('/admin/hospital-data', methods=['GET'])
def get_hospital_data():
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_admin, _ = verify_admin_token(token)
    if not is_admin:
        return jsonify({'message': 'Unauthorized'}), 403

    try:
        hospital_id = request.args.get('hospitalId')
        month_param = request.args.get('month')
        if not hospital_id or not month_param:
            return jsonify({'error': 'Missing hospitalId or month parameter'}), 400

        center_id = hospital_id
        year, mon = map(int, month_param.split("-"))
        _, num_days = monthrange(year, mon)
        
        all_data = {}
        for data_type in DATA_TYPES:
            doc_ref = db.collection("linac_data").document(center_id).collection("months").document(f"Month_{month_param}")
            doc = doc_ref.get()
            
            energy_dict = {e: [""] * num_days for e in ENERGY_TYPES}
            if doc.exists:
                firestore_field_name = f"data_{data_type}"
                doc_data = doc.to_dict().get(firestore_field_name, [])
                for row in doc_data:
                    energy, values = row.get("energy"), row.get("values", [])
                    if energy in energy_dict:
                        energy_dict[energy] = (values + [""] * num_days)[:num_days]

            table = [[e] + energy_dict[e] for e in ENERGY_TYPES]
            all_data[data_type] = table

        return jsonify({'data': all_data}), 200

    except Exception as e:
        app.logger.error(f"Admin get hospital data failed: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500

# --- ADMIN ROUTE FOR AUDIT LOGS ---
@app.route('/admin/audit-logs', methods=['GET'])
def get_audit_logs():
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_admin, _ = verify_admin_token(token)
    if not is_admin:
        return jsonify({'message': 'Unauthorized'}), 403

    try:
        logs_query = db.collection("audit_logs").order_by("timestamp", direction=firestore.Query.DESCENDING)

        hospital_id = request.args.get('hospitalId')
        action = request.args.get('action')
        date_str = request.args.get('date')

        if hospital_id:
            logs_query = logs_query.where('hospital', '==', hospital_id)
        if action:
            logs_query = logs_query.where('action', '==', action)
        if date_str:
            start_dt = datetime.strptime(date_str, '%Y-%m-%d').replace(tzinfo=pytz.UTC)
            end_dt = start_dt + timedelta(days=1)
            logs_query = logs_query.where('timestamp', '>=', start_dt).where('timestamp', '<', end_dt)
        
        logs_query = logs_query.limit(200)
        logs_snapshot = logs_query.stream()

        logs = []
        user_cache = {}
        for doc in logs_snapshot:
            log_data = doc.to_dict()
            if 'timestamp' in log_data and isinstance(log_data['timestamp'], datetime):
                log_data['timestamp'] = log_data['timestamp'].astimezone(pytz.timezone('Asia/Kolkata')).strftime('%Y-%m-%d %H:%M:%S')
            
            user_uid = log_data.get('userUid') or log_data.get('adminUid') or log_data.get('targetUserUid')
            if user_uid:
                if user_uid in user_cache:
                    log_data['user_display'] = user_cache[user_uid]
                else:
                    user_doc = db.collection('users').document(user_uid).get()
                    if user_doc.exists:
                        user_name = user_doc.to_dict().get('name', user_uid)
                        log_data['user_display'] = user_name
                        user_cache[user_uid] = user_name
                    else:
                        log_data['user_display'] = user_uid
                        user_cache[user_uid] = user_uid
            
            logs.append(log_data)
        
        return jsonify({"logs": logs}), 200

    except Exception as e:
        app.logger.error(f"Error loading audit logs: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e)
        return jsonify({'message': str(e)}), 500

# --- INDEX AND RUN ---
@app.route('/')
def index():
    return "✅ LINAC QA Backend Running"

if __name__ == '__main__':
    app.run(debug=True)
