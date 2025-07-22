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
from firebase_admin import credentials, firestore, auth, app_check # <-- NEW: Import app_check
import jwt # <-- NEW: Import for decoding errors

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

# --- NEW: APP CHECK VERIFICATION ---
# This function will run before every request to a protected endpoint.
@app.before_request
def verify_app_check_token():
    # The App Check token is passed in the 'X-Firebase-AppCheck' header.
    app_check_token = request.headers.get('X-Firebase-AppCheck')

    # Allow OPTIONS requests to pass through for CORS preflight.
    if request.method == 'OPTIONS':
        return None
    
    # Allow the root index page to be accessed without a token for health checks
    if request.path == '/':
        return None

    if not app_check_token:
        app.logger.warning("App Check token missing.")
        return jsonify({'error': 'Unauthorized: App Check token is missing'}), 401

    try:
        # Verify the token. If the token is invalid, this will raise an error.
        app_check.verify_token(app_check_token)
        # If verification succeeds, continue to the route handler.
        return None
    except (ValueError, jwt.exceptions.DecodeError) as e:
        app.logger.error(f"Invalid App Check token: {e}")
        return jsonify({'error': f'Unauthorized: Invalid App Check token'}), 401
    except Exception as e:
        app.logger.error(f"App Check verification failed with an unexpected error: {e}")
        return jsonify({'error': 'Unauthorized: App Check verification failed'}), 401


# Defined once here for consistency
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


# --- ALERT EMAIL ---
@app.route('/send-alert', methods=['POST'])
def send_alert():
    try:
        content = request.get_json(force=True)
        current_out_values = content.get("outValues", [])
        hospital = content.get("hospitalName", "Unknown")
        uid = content.get("uid")
        month_key = content.get("month")
        data_type = content.get("dataType", "output")
        tolerance_percent = content.get("tolerance", 2.0)
        
        data_type_display = data_type.replace("_", " ").title()

        if not uid or not month_key:
            return jsonify({'status': 'error', 'message': 'Missing UID or month for alert processing'}), 400

        user_doc = db.collection('users').document(uid).get()
        if not user_doc.exists:
            return jsonify({'status': 'error', 'message': 'User not found'}), 404
        user_data = user_doc.to_dict()
        center_id = user_data.get('centerId')

        if not center_id:
            return jsonify({'status': 'error', 'message': 'Center ID not found for user'}), 400

        rso_users = db.collection('users').where('centerId', '==', center_id).where('role', '==', 'RSO').stream()
        rso_emails = [rso.to_dict()['email'] for rso in rso_users if 'email' in rso.to_dict()]

        if not rso_emails:
            return jsonify({'status': 'no_rso_email', 'message': 'No RSO email found for this hospital.'}), 200

        if not APP_PASSWORD:
            return jsonify({'status': 'email_credentials_missing', 'message': 'Email credentials missing'}), 500

        month_alerts_doc_ref = db.collection("linac_alerts").document(center_id).collection("months").document(f"Month_{month_key}_{data_type}")
        alerts_doc_snap = month_alerts_doc_ref.get()
        
        previously_alerted = alerts_doc_snap.to_dict().get("alerted_values", []) if alerts_doc_snap.exists else []
        previously_alerted_strings = set(json.dumps(val, sort_keys=True) for val in previously_alerted)
        current_out_values_strings = set(json.dumps(val, sort_keys=True) for val in current_out_values)

        if current_out_values_strings == previously_alerted_strings:
            return jsonify({'status': 'no_change', 'message': 'No new alerts or changes. Email not sent.'})

        message_body = f"{data_type_display} QA Status Update for {hospital} ({month_key})\n\n"
        if current_out_values:
            message_body += f"Current Out-of-Tolerance Values (±{tolerance_percent}%):\n\n"
            for v in sorted(current_out_values, key=lambda x: (x.get('energy'), x.get('date'))):
                message_body += f"Energy: {v.get('energy', 'N/A')}, Date: {v.get('date', 'N/A')}, Value: {v.get('value', 'N/A')}%\n"
        else:
            message_body += f"All previously detected {data_type_display} QA issues for this month are now resolved.\n"

        email_sent = send_notification_email(", ".join(rso_emails), f"⚠ {data_type_display} QA Status - {hospital} ({month_key})", message_body)

        if email_sent:
            month_alerts_doc_ref.set({"alerted_values": current_out_values}, merge=False)
            return jsonify({'status': 'alert sent'}), 200
        else:
            return jsonify({'status': 'email_send_error', 'message': 'Failed to send email.'}), 500
    except Exception as e:
        app.logger.error(f"Error in send_alert function: {str(e)}", exc_info=True)
        if SENTRY_DSN: sentry_sdk.capture_exception(e)
        return jsonify({'status': 'error', 'message': str(e)}), 500


# --- CHATBOT ---
@app.route('/query-qa-data', methods=['POST'])
def query_qa_data():
    try:
        content = request.get_json(force=True)
        user_query_text = content.get("query_text", "")
        month_param = content.get("month")
        uid = content.get("uid")
        data_type_context = content.get("dataType", "output")

        if not user_query_text or not month_param or not uid:
            return jsonify({'status': 'error', 'message': 'Missing query text, month, or UID'}), 400

        user_doc = db.collection("users").document(uid).get()
        if not user_doc.exists: return jsonify({'status': 'error', 'message': 'User not found'}), 404
        center_id = user_doc.to_dict().get("centerId")
        if not center_id: return jsonify({'status': 'error', 'message': 'Missing centerId for user'}), 400
        
        lower_case_query = user_query_text.lower()
        
        query_data_type = data_type_context
        if 'flatness' in lower_case_query: query_data_type = 'flatness'
        elif 'inline' in lower_case_query: query_data_type = 'inline'
        elif 'crossline' in lower_case_query: query_data_type = 'crossline'
        elif 'output' in lower_case_query: query_data_type = 'output'
        
        config = DATA_TYPE_CONFIGS[query_data_type]
        warning_threshold = config["warning"]
        tolerance_threshold = config["tolerance"]

        doc = db.collection("linac_data").document(center_id).collection("months").document(f"Month_{month_param}").get()
        data_rows = doc.to_dict().get(f"data_{query_data_type}", []) if doc.exists else []

        year, mon = map(int, month_param.split("-"))
        date_strings = [f"{year}-{str(mon).zfill(2)}-{str(i+1).zfill(2)}" for i in range(monthrange(year, mon)[1])]
        
        if "out of tolerance" in lower_case_query:
            out_dates = set()
            for row in data_rows:
                for i, value in enumerate(row.get("values", [])):
                    try:
                        if abs(float(value)) > tolerance_threshold: out_dates.add(date_strings[i])
                    except (ValueError, TypeError): pass
            msg = f"Out of tolerance dates for {query_data_type.title()}: {', '.join(sorted(list(out_dates))) if out_dates else 'None.'}"
            return jsonify({'status': 'success', 'message': msg}), 200

        if "warning values" in lower_case_query:
            warnings = []
            for row in data_rows:
                for i, value in enumerate(row.get("values", [])):
                    try:
                        val_float = float(value)
                        if warning_threshold < abs(val_float) <= tolerance_threshold:
                            warnings.append(f"{row.get('energy')} on {date_strings[i]}: {val_float}%")
                    except (ValueError, TypeError): pass
            msg = f"Warning values for {query_data_type.title()}: {'; '.join(warnings) if warnings else 'None.'}"
            return jsonify({'status': 'success', 'message': msg}), 200

        return jsonify({'status': 'error', 'message': f"I'm sorry, I can't answer that yet. Try asking about 'out of tolerance' or 'warning values' for {query_data_type.title()}."}), 501

    except Exception as e:
        app.logger.error(f"Chatbot query failed: {str(e)}", exc_info=True)
        if sentry_sdk_configured: sentry_sdk.capture_exception(e)
        return jsonify({'status': 'error', 'message': str(e)}), 500


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
            "hospital": old_user_data.get("hospital", "N/A") # *** FIX: ADD HOSPITAL TO LOG ***
        }

        if "status" in updates:
            audit_entry["changes"]["status"] = {"old": old_user_data.get("status"), "new": updates["status"]}
            audit_entry["oldData"]["status"] = old_user_data.get("status")
            audit_entry["newData"]["status"] = updates["status"]
        if "role" in updates:
            audit_entry["changes"]["role"] = {"old": old_user_data.get("role"), "new": updates["role"]}
            audit_entry["oldData"]["role"] = old_user_data.get("role")
            audit_entry["newData"]["role"] = updates["role"]
        if "hospital" in updates:
            audit_entry["changes"]["hospital"] = {"old": old_user_data.get("hospital"), "new": updates["hospital"]}
            audit_entry["oldData"]["hospital"] = old_user_data.get("hospital")
            audit_entry["newData"]["hospital"] = updates["hospital"]
        
        audit_entry["targetUserEmail"] = old_user_data.get("email", "N/A")
        audit_entry["targetUserName"] = old_user_data.get("name", "N/A")

        db.collection("audit_logs").add(audit_entry)
        app.logger.info(f"Audit: User {uid} updated by {requesting_admin_uid}")

        updated_user_data = ref.get().to_dict()
        if updated_user_data.get("email"):
            subject = "LINAC QA Account Update"
            body = f"Your LINAC QA account details have been updated."
            
            if "status" in updates:
                status_text = updates["status"].upper()
                body += f"\nYour account status is now: {status_text}."
                if status_text == "ACTIVE":
                    body += " You can now log in and use the portal."
                elif status_text == "REJECTED":
                    body += " Please contact support for more information."
            
            if "role" in updates:
                 body += f"\nYour role has been updated to: {updates['role']}."
            if "hospital" in updates:
                 body += f"\nYour hospital has been updated to: {updates['hospital']}."

            send_notification_email(updated_user_data["email"], subject, body)
        else:
            app.logger.warning(f"No email for user {uid} found to send update notification.")
            if sentry_sdk_configured:
                sentry_sdk.capture_message(f"No email for user {uid} found to send update notification.", level="warning")

        return jsonify({'status': 'success', 'message': 'User updated successfully'}), 200
    except Exception as e:
        app.logger.error(f"Error updating user status/role/hospital: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e)
        return jsonify({'message': f"Failed to delete user: {str(e)}"}), 500

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
        user_doc = user_doc_ref.get()
        user_data_to_log = user_doc.to_dict() if user_doc.exists else {}

        try:
            auth.delete_user(uid_to_delete)
            app.logger.info(f"Firebase Auth user {uid_to_delete} deleted.")
        except Exception as e:
            if "User record not found" in str(e):
                app.logger.warning(f"Firebase Auth user {uid_to_delete} not found, proceeding with Firestore deletion.")
                if sentry_sdk_configured:
                    sentry_sdk.capture_message(f"Firebase Auth user {uid_to_delete} not found during deletion attempt.", level="warning")
            else:
                app.logger.error(f"Error deleting Firebase Auth user {uid_to_delete}: {str(e)}", exc_info=True)
                if sentry_sdk_configured:
                    sentry_sdk.capture_exception(e)
                return jsonify({'message': f"Failed to delete Firebase Auth user: {str(e)}"}), 500

        if user_doc.exists:
            user_doc_ref.delete()
            app.logger.info(f"Firestore user document {uid_to_delete} ({user_data_to_log.get('email')}) deleted.")
        else:
            app.logger.warning(f"Firestore user document {uid_to_delete} not found (already deleted?).")
            if sentry_sdk_configured:
                sentry_sdk.capture_message(f"Firestore user document {uid_to_delete} not found during deletion attempt.", level="warning")
        
        audit_entry = {
            "timestamp": firestore.SERVER_TIMESTAMP,
            "adminUid": requesting_admin_uid,
            "action": "user_deletion",
            "targetUserUid": uid_to_delete,
            "deletedUserData": user_data_to_log,
            "hospital": user_data_to_log.get("hospital", "N/A") # *** FIX: ADD HOSPITAL TO LOG ***
        }
        db.collection("audit_logs").add(audit_entry)
        app.logger.info(f"Audit: User {uid_to_delete} deleted by {requesting_admin_uid}")

        return jsonify({'status': 'success', 'message': 'User deleted successfully'}), 200

    except Exception as e:
        app.logger.error(f"Error deleting user: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e)
        return jsonify({'message': f"Failed to delete user: {str(e)}"}), 500

@app.route('/admin/hospital-data', methods=['GET', 'OPTIONS'])
def get_hospital_qa_data():
    if request.method == 'OPTIONS':
        return '', 200

    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_admin, _ = verify_admin_token(token)
    if not is_admin:
        return jsonify({'message': 'Unauthorized'}), 403

    hospital_id = request.args.get('hospitalId')
    month_param = request.args.get('month')

    if not hospital_id or not month_param:
        return jsonify({'message': 'Missing hospitalId or month parameter'}), 400

    try:
        year, mon = map(int, month_param.split("-"))
        _, num_days = monthrange(year, mon)

        doc_ref = db.collection("linac_data").document(hospital_id).collection("months").document(f"Month_{month_param}")
        doc_snap = doc_ref.get()

        all_data_tables = {}
        
        firestore_data = doc_snap.to_dict() if doc_snap.exists else {}

        for data_type in DATA_TYPES:
            field_name = f"data_{data_type}"
            energy_dict = {e: [""] * num_days for e in ENERGY_TYPES}
            
            if field_name in firestore_data:
                for row in firestore_data[field_name]:
                    energy = row.get("energy")
                    values = row.get("values", [])
                    if energy in energy_dict:
                        energy_dict[energy] = (values + [""] * num_days)[:num_days]
            
            all_data_tables[data_type] = [[e] + energy_dict[e] for e in ENERGY_TYPES]

        return jsonify({'status': 'success', 'data': all_data_tables}), 200
    except Exception as e:
        app.logger.error(f"Error fetching hospital QA data for admin: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e)
        return jsonify({'error': f"Failed to fetch data: {str(e)}"}), 500

@app.route('/admin/audit-logs', methods=['GET', 'OPTIONS'])
def get_audit_logs():
    if request.method == 'OPTIONS':
        return '', 200

    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_admin, _ = verify_admin_token(token)
    if not is_admin:
        return jsonify({'message': 'Unauthorized'}), 403

    hospital_filter = request.args.get('hospitalId')
    date_filter_str = request.args.get('date')
    action_filter = request.args.get('action')

    try:
        logs_query = db.collection("audit_logs")
        
        user_timezone = pytz.timezone('Asia/Kolkata')
        utc_timezone = pytz.utc

        if date_filter_str:
            start_of_day_naive = datetime.strptime(date_filter_str, "%Y-%m-%d")
            start_of_day_local = user_timezone.localize(start_of_day_naive)
            end_of_day_local = start_of_day_local + timedelta(days=1)
            start_of_day_utc = start_of_day_local.astimezone(utc_timezone)
            end_of_day_utc = end_of_day_local.astimezone(utc_timezone)
            
            logs_query = logs_query.where('timestamp', '>=', start_of_day_utc)
            logs_query = logs_query.where('timestamp', '<', end_of_day_utc)

        # *** DEFINITIVE FIX: REMOVED ALL QUERY FILTERS EXCEPT DATE ***
        # *** AND PERFORM FILTERING IN PYTHON AFTER FETCHING ***
        if action_filter:
            logs_query = logs_query.where('action', '==', action_filter)

        logs_query = logs_query.order_by('timestamp', direction=firestore.Query.DESCENDING)
        
        all_logs = []
        for doc in logs_query.stream():
            log_data = doc.to_dict()

            # *** NEW: Manual filtering for hospital after fetching data ***
            if hospital_filter and log_data.get('hospital', '').lower() != hospital_filter.lower():
                continue

            if 'timestamp' in log_data and log_data['timestamp'] is not None:
                utc_dt = log_data['timestamp'].astimezone(utc_timezone)
                ist_dt = utc_dt.astimezone(user_timezone)
                log_data['timestamp'] = ist_dt.strftime("%d/%m/%Y, %I:%M:%S %p")
            else:
                log_data['timestamp'] = 'No Timestamp'
            
            if 'targetUserName' in log_data:
                log_data['user_display'] = f"{log_data['targetUserName']} ({log_data.get('targetUserEmail', 'N/A')})"
            elif 'userEmail' in log_data:
                log_data['user_display'] = log_data['userEmail']
            else:
                log_data['user_display'] = 'N/A'
            all_logs.append(log_data)

        return jsonify({'status': 'success', 'logs': all_logs}), 200
    except Exception as e:
        app.logger.error(f"Error fetching audit logs: {str(e)}", exc_info=True)
        if SENTRY_DSN: sentry_sdk.capture_exception(e)
        return jsonify({'message': f"Failed to fetch audit logs: {str(e)}"}), 500

# --- EXCEL EXPORT ---
@app.route('/export-excel', methods=['POST'])
def export_excel():
    try:
        content = request.get_json(force=True)
        uid = content.get("uid")
        month_param = content.get("month")
        data_type = content.get("dataType")

        if not uid or not month_param:
            return jsonify({'error': 'Missing UID or month parameter'}), 400
        if not data_type or data_type not in DATA_TYPES:
            return jsonify({'error': 'Invalid or missing dataType'}), 400

        user_doc = db.collection("users").document(uid).get()
        if not user_doc.exists:
            return jsonify({'error': 'User not found'}), 404
        user_data = user_doc.to_dict()
        center_id = user_data.get("centerId")

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
        
        data_for_df = []
        columns = ['Energy']
        for i in range(1, num_days + 1):
            columns.append(f"{year}-{str(mon).zfill(2)}-{str(i).zfill(2)}")

        for energy_type in ENERGY_TYPES:
            row_data = [energy_type] + energy_dict[energy_type]
            data_for_df.append(row_data)

        df = pd.DataFrame(data_for_df, columns=columns)

        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            sheet_name = data_type.title() + ' Data'
            df.to_excel(writer, index=False, sheet_name=sheet_name)
        output.seek(0)

        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            download_name=f'LINAC_QA_{data_type.upper()}_{month_param}.xlsx',
            as_attachment=True
        )

    except Exception as e:
        app.logger.error(f"Error exporting Excel file: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e)
        return jsonify({'error': f"Failed to export Excel file: {str(e)}"}), 500


# --- ANNOTATIONS ---
@app.route('/annotations', methods=['GET'])
def get_annotations():
    try:
        month_param = request.args.get('month')
        data_type = request.args.get('dataType')
        uid = request.args.get('uid')
        if not all([month_param, data_type, uid]): return jsonify({'error': 'Missing parameters'}), 400

        user_doc = db.collection("users").document(uid).get()
        if not user_doc.exists: return jsonify({'error': 'User not found'}), 404
        center_id = user_doc.to_dict().get("centerId")
        if not center_id: return jsonify({'error': 'Missing centerId'}), 400

        annotations_ref = db.collection("linac_annotations").document(center_id).collection("months").document(f"Month_{month_param}")
        doc = annotations_ref.get()
        if doc.exists:
            all_annotations = doc.to_dict()
            type_annotations = {k: v for k, v in all_annotations.items() if v.get('dataType') == data_type}
            return jsonify(type_annotations), 200
        else:
            return jsonify({}), 200
    except Exception as e:
        app.logger.error(f"Get annotations failed: {str(e)}", exc_info=True)
        if SENTRY_DSN: sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500

@app.route('/save-annotation', methods=['POST'])
def save_annotation():
    try:
        content = request.get_json(force=True)
        uid, month_param, key, data = content.get("uid"), content.get("month"), content.get("key"), content.get("data")
        if not all([uid, month_param, key, data]): return jsonify({'status': 'error', 'message': 'Missing data'}), 400
        user_doc = db.collection("users").document(uid).get()
        if not user_doc.exists: return jsonify({'status': 'error', 'message': 'User not found'}), 404
        center_id = user_doc.to_dict().get("centerId")
        if not center_id: return jsonify({'status': 'error', 'message': 'Missing centerId'}), 400

        doc_ref = db.collection("linac_annotations").document(center_id).collection("months").document(f"Month_{month_param}")
        doc_ref.set({key: data}, merge=True)
        return jsonify({'status': 'success'}), 200
    except Exception as e:
        app.logger.error(f"Save annotation failed: {str(e)}", exc_info=True)
        if SENTRY_DSN: sentry_sdk.capture_exception(e)
        return jsonify({'status': 'error', 'message': str(e)}), 500
        
@app.route("/debug-sentry")
def trigger_error():
    division_by_zero = 1 / 0
    return "Hello, world!"

# --- INDEX ---
@app.route('/')
def index():
    return "✅ LINAC QA Backend Running"

# --- RUN ---
if __name__ == '__main__':
    app.run(debug=True)
