# app.py

import sentry_sdk
from sentry_sdk.integrations.flask import FlaskIntegration
import os
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
import pytz
import uuid
import firebase_admin
from firebase_admin import credentials, firestore, auth, app_check
import jwt
import pandas as pd
from io import BytesIO
from prophet import Prophet
from scipy import stats
import numpy as np 

# --- SENTRY INTEGRATION ---
SENTRY_DSN = os.environ.get("SENTRY_DSN")

sentry_sdk_configured = False
if SENTRY_DSN:
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[FlaskIntegration()],
        traces_sample_rate=1.0,
        profiles_sample_rate=1.0,
        send_default_pii=False # IMPORTANT: Set to False to protect user data
    )
    sentry_sdk_configured = True
    print("Sentry initialized successfully.")
else:
    print("SENTRY_DSN environment variable not set. Sentry not initialized.")


app = Flask(__name__)

# --- CORS CONFIGURATION ---
origins = [
    "https://front-endnew.onrender.com",
    "http://127.0.0.1:5500",
    "http://localhost:5500"
]
CORS(app, resources={r"/*": {"origins": origins}})

app.logger.setLevel(logging.DEBUG)

# --- EMAIL CONFIG ---
# [FIXED] Corrected 'SENTRY_EMAIL' to 'SENDER_EMAIL'
SENDER_EMAIL = os.environ.get('SENDER_EMAIL', 'itsmealwin12@gmail.com')
RECEIVER_EMAIL = os.environ.get('RECEIVER_EMAIL', 'alwinjose812@gmail.com')
APP_PASSWORD = os.environ.get('EMAIL_APP_PASSWORD')

# --- EMAIL SENDER FUNCTION ---
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
        # [MODIFIED] Added an explicit timeout to the SMTP connection
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=20)
        server.login(SENDER_EMAIL, APP_PASSWORD)
        server.send_message(msg)
        server.quit()
        app.logger.info(f"📧 Notification sent to {recipient_email}")
        return True
    except Exception as e:
        app.logger.error(f"❌ Email error: {str(e)} for recipient {recipient_email}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e)
        return False

# --- FIREBASE INIT ---
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
    public_paths = ['/', '/public/groups', '/public/institutions-by-group', '/public/all-institutions']
    
    if request.method == 'OPTIONS' or request.path in public_paths:
        return None
        
    app_check_token = request.headers.get('X-Firebase-AppCheck')
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

# --- CONSTANTS & DEFAULTS ---
DATA_TYPES = ["output", "flatness", "inline", "crossline"]
DEFAULT_ENERGY_TYPES = ["6X", "10X", "15X", "6X FFF", "10X FFF", "6E", "9E", "12E", "15E", "18E"]
DEFAULT_TOLERANCES = {
    "output": {"warning": 1.8, "tolerance": 2.0, "yAxisMin": -3, "yAxisMax": 3},
    "flatness": {"warning": 0.9, "tolerance": 1.0, "yAxisMin": -2, "yAxisMax": 2},
    "inline": {"warning": 0.9, "tolerance": 1.0, "yAxisMin": -2, "yAxisMax": 2},
    "crossline": {"warning": 0.9, "tolerance": 1.0, "yAxisMin": -2, "yAxisMax": 2}
}

# --- [MODIFIED] SETTINGS MANAGEMENT ---
def get_machine_settings(machine_id):
    """Fetches settings for a machine, returns defaults if none exist."""
    if not machine_id:
        return {"tolerances": DEFAULT_TOLERANCES, "energyTypes": DEFAULT_ENERGY_TYPES}
        
    settings_doc = db.collection('settings').document(machine_id).get()
    if settings_doc.exists:
        settings = settings_doc.to_dict()
        final_tolerances = DEFAULT_TOLERANCES.copy()
        if "tolerances" in settings:
            for key, value in settings["tolerances"].items():
                if key in final_tolerances:
                    final_tolerances[key].update(value)

        return {
            "tolerances": final_tolerances,
            "energyTypes": settings.get("energyTypes", DEFAULT_ENERGY_TYPES)
        }
    return {"tolerances": DEFAULT_TOLERANCES, "energyTypes": DEFAULT_ENERGY_TYPES}

@app.route('/settings/<machine_id>', methods=['GET'])
def get_settings_endpoint(machine_id):
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_valid, _, _ = verify_user_token(token)
    if not is_valid:
        return jsonify({'error': 'Unauthorized'}), 403
    
    settings = get_machine_settings(machine_id)
    return jsonify(settings), 200

@app.route('/settings/<machine_id>', methods=['POST'])
def save_settings_endpoint(machine_id):
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_valid, _, _ = verify_user_token(token)
    if not is_valid:
        return jsonify({'error': 'Unauthorized'}), 403

    try:
        new_settings = request.get_json(force=True)
        if "tolerances" not in new_settings or "energyTypes" not in new_settings:
            return jsonify({'status': 'error', 'message': 'Invalid settings format.'}), 400
            
        db.collection('settings').document(machine_id).set(new_settings)
        return jsonify({'status': 'success', 'message': 'Settings saved successfully.'}), 200
    except Exception as e:
        app.logger.error(f"Error saving settings for {machine_id}: {str(e)}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500

# --- GENERIC USER TOKEN VERIFICATION ---
def verify_user_token(id_token):
    """Verifies a generic user token and returns their UID and user data."""
    try:
        decoded_token = auth.verify_id_token(id_token)
        uid = decoded_token['uid']
        user_doc = db.collection('users').document(uid).get()
        if user_doc.exists:
            return True, uid, user_doc.to_dict()
    except Exception as e:
        app.logger.error(f"User token verification failed: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e)
    return False, None, None

# --- VERIFY ADMIN TOKEN ---
def verify_admin_token(id_token):
    try:
        decoded_token = auth.verify_id_token(id_token)
        uid = decoded_token['uid']
        user_doc = db.collection('users').document(uid).get()
        user_data = user_doc.to_dict()
        if user_doc.exists and user_data.get('role') in ['Admin', 'Super Admin']:
            return True, uid, user_data
    except Exception as e:
        app.logger.error(f"Token verification failed: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e)
    return False, None, None

# --- NEW PUBLIC ENDPOINTS FOR DYNAMIC SIGNUP ---
@app.route('/public/groups', methods=['GET'])
def get_public_groups():
    try:
        institutions_ref = db.collection('institutions').stream()
        groups = {doc.to_dict().get('parentGroup') for doc in institutions_ref if doc.to_dict().get('parentGroup')}
        return jsonify(sorted(list(groups))), 200
    except Exception as e:
        app.logger.error(f"Error fetching public groups: {str(e)}", exc_info=True)
        return jsonify({'message': 'Could not retrieve organization list.'}), 500

@app.route('/public/institutions-by-group', methods=['GET'])
def get_public_institutions_by_group():
    group_id = request.args.get('group')
    if not group_id:
        return jsonify({'message': 'Group ID is required.'}), 400
    try:
        institutions_ref = db.collection('institutions').where('parentGroup', '==', group_id).stream()
        institutions = [{'name': doc.to_dict().get('name'), 'centerId': doc.to_dict().get('centerId')} for doc in institutions_ref]
        institutions.sort(key=lambda x: x.get('name', ''))
        return jsonify(institutions), 200
    except Exception as e:
        app.logger.error(f"Error fetching institutions for group {group_id}: {str(e)}", exc_info=True)
        return jsonify({'message': 'Could not retrieve institution list.'}), 500

# --- NEW PUBLIC ENDPOINT FOR ALL INSTITUTIONS (FOR PROFILE PAGE) ---
@app.route('/public/all-institutions', methods=['GET'])
def get_all_institutions():
    try:
        institutions_ref = db.collection('institutions').stream()
        institutions = [{'name': doc.to_dict().get('name'), 'centerId': doc.to_dict().get('centerId')} for doc in institutions_ref]
        institutions.sort(key=lambda x: x.get('name', ''))
        return jsonify(institutions), 200
    except Exception as e:
        app.logger.error(f"Error fetching all institutions: {str(e)}", exc_info=True)
        return jsonify({'message': 'Could not retrieve institution list.'}), 500

# --- MODIFIED ANNOTATION ENDPOINTS (MACHINE-AWARE) ---
@app.route('/save-annotation', methods=['POST'])
def save_annotation():
    try:
        content = request.get_json(force=True)
        machine_id = content.get("machineId")
        month = content.get("month")
        key = content.get("key")
        data = content.get("data")

        if not all([machine_id, month, key, data]):
            return jsonify({'status': 'error', 'message': 'Missing required fields'}), 400

        annotation_ref = db.collection('linac_data').document(machine_id).collection('annotations').document(month).collection('keys').document(key)
        annotation_ref.set(data)

        is_service_event = data.get('isServiceEvent', False)
        event_date = data.get('eventDate')
        
        if event_date:
            service_event_ref = db.collection('linac_data').document(machine_id).collection('service_events').document(event_date)
            if is_service_event:
                service_event_ref.set({
                    'description': data.get('text', 'Service/Calibration'),
                    'energy': data.get('energy'),
                    'dataType': data.get('dataType')
                })
                app.logger.info(f"Service event marked for machine {machine_id} on date {event_date}")
            else:
                service_event_ref.delete()
                app.logger.info(f"Service event unmarked for machine {machine_id} on date {event_date}")

        return jsonify({'status': 'success', 'message': 'Annotation saved successfully'}), 200
    except Exception as e:
        app.logger.error(f"Save annotation failed: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/delete-annotation', methods=['POST'])
def delete_annotation():
    try:
        content = request.get_json(force=True)
        machine_id = content.get("machineId")
        month = content.get("month")
        key = content.get("key")

        if not all([machine_id, month, key]):
            return jsonify({'status': 'error', 'message': 'Missing required fields'}), 400
        
        annotation_ref = db.collection('linac_data').document(machine_id).collection('annotations').document(month).collection('keys').document(key)
        annotation_ref.delete()

        event_date = key.split('-', 1)[1]
        if event_date:
            service_event_ref = db.collection('linac_data').document(machine_id).collection('service_events').document(event_date)
            service_event_ref.delete()
            app.logger.info(f"Deleted service event for machine {machine_id} on date {event_date} along with annotation.")

        return jsonify({'status': 'success', 'message': 'Annotation deleted successfully'}), 200
    except Exception as e:
        app.logger.error(f"Delete annotation failed: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e)
        return jsonify({'status': 'error', 'message': str(e)}), 500

# --- USER MANAGEMENT ENDPOINTS ---
@app.route('/signup', methods=['POST'])
def signup():
    try:
        user_data = request.get_json(force=True)
        required = ['name', 'email', 'hospital', 'role', 'uid', 'status']
        if any(f not in user_data or not user_data[f] for f in required):
            return jsonify({'status': 'error', 'message': 'Missing required fields'}), 400

        institution_doc = db.collection('institutions').document(user_data['hospital']).get()
        if not institution_doc.exists:
            return jsonify({'status': 'error', 'message': 'Selected institution not found.'}), 404
        
        parent_group = institution_doc.to_dict().get('parentGroup')
        if not parent_group:
            return jsonify({'status': 'error', 'message': 'Institution is not associated with a parent group.'}), 400

        user_ref = db.collection('users').document(user_data['uid'])
        if user_ref.get().exists:
            return jsonify({'status': 'error', 'message': 'User already exists'}), 409
            
        user_ref.set({
            'name': user_data['name'],
            'email': user_data['email'].strip().lower(),
            'hospital': user_data['hospital'],
            'role': user_data['role'],
            'centerId': user_data['hospital'],
            'status': user_data['status'],
            'parentGroup': parent_group
        })
        return jsonify({'status': 'success', 'message': 'User registered'}), 200
    except Exception as e:
        app.logger.error(f"Signup failed: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e)
        return jsonify({'status': 'error', 'message': str(e)}), 500

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

        audit_entry = {
            "timestamp": firestore.SERVER_TIMESTAMP,
            "action": "user_login",
            "targetUserUid": uid,
            "hospital": user_data.get("hospital", "N/A"),
            "details": {
                "user_email": user_data.get("email", "N/A"),
                "user_agent": request.headers.get('User-Agent')
            }
        }
        db.collection("audit_logs").add(audit_entry)

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
            'centerId': new_hospital
        }
        
        user_ref.update(updates)

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

# --- HELPER FUNCTION FOR PROACTIVE CHAT ---
def find_new_warnings(old_data, new_data, config):
    old_warnings = set()
    for row in old_data:
        energy = row.get("energy")
        for i, value in enumerate(row.get("values", [])):
            try:
                val = abs(float(value))
                if val >= config["warning"] and val <= config["tolerance"]:
                    old_warnings.add(f"{energy}-{i}")
            except (ValueError, TypeError):
                continue
    
    new_warnings = []
    for row in new_data:
        energy = row[0]
        for i, value in enumerate(row[1:]):
            try:
                val = abs(float(value))
                if val >= config["warning"] and val <= config["tolerance"]:
                    if f"{energy}-{i}" not in old_warnings:
                        new_warnings.append({"energy": energy, "value": val})
            except (ValueError, TypeError):
                continue
    return new_warnings

# --- DATA & ALERT ENDPOINTS ---
@app.route('/save', methods=['POST'])
def save_data():
    try:
        content = request.get_json(force=True)
        uid = content.get("uid")
        month_param = content.get("month")
        raw_data = content.get("data")
        data_type = content.get("dataType")
        machine_id = content.get("machineId") 

        if not data_type or data_type not in DATA_TYPES:
            return jsonify({'status': 'error', 'message': 'Invalid or missing dataType'}), 400
        if not machine_id:
            return jsonify({'status': 'error', 'message': 'machineId is required'}), 400

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
        firestore_field_name = f"data_{data_type}"
        doc_ref = db.collection("linac_data").document(machine_id).collection("months").document(month_doc_id)
        
        old_data_doc = doc_ref.get()
        old_data = old_data_doc.to_dict().get(firestore_field_name, []) if old_data_doc.exists else []
        
        machine_settings = get_machine_settings(machine_id)
        current_data_type_config = machine_settings.get("tolerances", {}).get(data_type)

        if current_data_type_config:
            new_warnings = find_new_warnings(old_data, raw_data, current_data_type_config)
            
            if new_warnings:
                first_warning = new_warnings[0]
                topic = "output_drift" if data_type == "output" else "flatness_warning"
                db.collection("proactive_chats").add({
                    "uid": uid,
                    "read": False,
                    "timestamp": firestore.SERVER_TIMESTAMP,
                    "initial_message": f"I noticed a new warning for {first_warning['energy']} ({data_type.title()}). The value was {first_warning['value']}%. Would you like help diagnosing this?",
                    "topic": topic
                })
                app.logger.info(f"Proactive chat triggered for user {uid} due to new warnings.")

        converted = [{"row": i, "energy": row[0], "values": row[1:]} for i, row in enumerate(raw_data) if len(row) > 1]
        doc_ref.set({firestore_field_name: converted}, merge=True)
        
        return jsonify({'status': 'success', 'message': f'{data_type} data saved successfully'}), 200

    except Exception as e:
        app.logger.error(f"Save data failed for {data_type}: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e)
        return jsonify({'status': 'error', 'message': str(e)}), 500

# --- ENDPOINT FOR SAVING DAILY ENVIRONMENTAL DATA (MACHINE-AWARE) ---
@app.route('/save-daily-env', methods=['POST'])
def save_daily_env():
    try:
        content = request.get_json(force=True)
        machine_id = content.get("machineId")
        date = content.get("date")
        temperature = content.get("temperature")
        pressure = content.get("pressure")

        if not all([machine_id, date]):
            return jsonify({'status': 'error', 'message': 'Missing machineId or date'}), 400
        
        update_data = {}
        if temperature is not None:
            try:
                update_data['temperature_celsius'] = float(temperature)
            except (ValueError, TypeError):
                pass
        if pressure is not None:
            try:
                update_data['pressure_hpa'] = float(pressure)
            except (ValueError, TypeError):
                pass

        if not update_data:
             return jsonify({'status': 'no_change', 'message': 'No valid data to save'}), 200

        doc_ref = db.collection("linac_data").document(machine_id).collection("daily_env").document(date)
        doc_ref.set(update_data, merge=True)

        return jsonify({'status': 'success', 'message': f'Environmental data for {date} saved'}), 200

    except Exception as e:
        app.logger.error(f"Save daily env data failed: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e)
        return jsonify({'status': 'error', 'message': str(e)}), 500

# --- DATA FETCHING ENDPOINT (MACHINE-AWARE & SETTINGS-AWARE) ---
@app.route('/data', methods=['GET'])
def get_data():
    try:
        month_param = request.args.get('month')
        uid = request.args.get('uid')
        data_type = request.args.get('dataType')
        machine_id = request.args.get('machineId')

        if not all([month_param, uid, data_type, machine_id]):
            return jsonify({'error': 'Missing month, uid, dataType, or machineId'}), 400

        user_doc = db.collection("users").document(uid).get()
        if not user_doc.exists:
            return jsonify({'error': 'User not found'}), 404
        user_status = user_doc.to_dict().get("status", "pending")

        if user_status != "active":
            return jsonify({'error': 'Account not active'}), 403

        year, mon = map(int, month_param.split("-"))
        _, num_days = monthrange(year, mon)
        
        machine_settings = get_machine_settings(machine_id)
        energy_types_for_machine = machine_settings.get("energyTypes", DEFAULT_ENERGY_TYPES)
        energy_dict = {e: [""] * num_days for e in energy_types_for_machine}
        
        firestore_field_name = f"data_{data_type}"
        
        doc = db.collection("linac_data").document(machine_id).collection("months").document(f"Month_{month_param}").get()

        if doc.exists:
            doc_data = doc.to_dict().get(firestore_field_name, [])
            for row in doc_data:
                energy, values = row.get("energy"), row.get("values", [])
                if energy in energy_dict:
                    energy_dict[energy] = (values + [""] * num_days)[:num_days]

        table = [[e] + energy_dict[e] for e in energy_types_for_machine]
        
        env_data = {}
        env_docs = db.collection("linac_data").document(machine_id).collection("daily_env").stream()
        for doc in env_docs:
            if doc.id.startswith(month_param):
                env_data[doc.id] = doc.to_dict()

        return jsonify({'data': table, 'env_data': env_data, 'settings': machine_settings}), 200
    except Exception as e:
        app.logger.error(f"Get data failed for {data_type}: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500

# --- NEW EXPORT TO EXCEL ENDPOINT ---
@app.route('/export-excel', methods=['POST'])
def export_excel():
    try:
        content = request.get_json(force=True)
        month_param = content.get('month')
        uid = content.get('uid')
        data_type = content.get('dataType')
        machine_id = content.get('machineId')

        if not all([month_param, uid, data_type, machine_id]):
            return jsonify({'error': 'Missing required parameters'}), 400

        doc_ref = db.collection("linac_data").document(machine_id).collection("months").document(f"Month_{month_param}")
        doc = doc_ref.get()

        if not doc.exists:
            return jsonify({'error': 'No data found for the selected machine and month'}), 404

        firestore_field_name = f"data_{data_type}"
        doc_data = doc.to_dict().get(firestore_field_name, [])
        
        if not doc_data:
            return jsonify({'error': f'No {data_type} data found for the selected period'}), 404

        year, mon = map(int, month_param.split("-"))
        _, num_days = monthrange(year, mon)
        
        data_for_df = []
        for row in doc_data:
            energy = row.get("energy")
            values = row.get("values", [])
            padded_values = (values + [""] * num_days)[:num_days]
            data_for_df.append([energy] + padded_values)

        columns = ["Energy"] + list(range(1, num_days + 1))
        df = pd.DataFrame(data_for_df, columns=columns)

        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name=f'{data_type.title()} Data')
        output.seek(0)
        
        return send_file(
            output,
            download_name=f'LINAC_QA_{data_type.upper()}_{month_param}.xlsx',
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True
        )
    except Exception as e:
        app.logger.error(f"Excel export failed for {data_type}: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500

# --- CORRECTED SEND ALERT ENDPOINT ---
@app.route('/send-alert', methods=['POST'])
def send_alert():
    try:
        content = request.get_json(force=True)
        uid = content.get("uid")
        machine_id = content.get("machineId")

        if not all([uid, machine_id]):
            return jsonify({'status': 'error', 'message': 'Missing uid or machineId'}), 400

        user_doc = db.collection('users').document(uid).get()
        if not user_doc.exists:
            return jsonify({'status': 'error', 'message': 'User not found'}), 404
        
        user_data = user_doc.to_dict()
        center_id = user_data.get('centerId') 

        if not center_id:
             return jsonify({'status': 'error', 'message': 'Center ID not found for user'}), 400

        rso_users_query = db.collection('users').where('centerId', '==', center_id).where('role', '==', 'RSO')
        rso_users_stream = rso_users_query.stream()
        
        recipient_emails = [rso.to_dict()['email'] for rso in rso_users_stream if 'email' in rso.to_dict()]
        
        if not recipient_emails:
            app.logger.warning(f"No RSO found for centerId {center_id}. Cannot send alert.")
            return jsonify({'status': 'no_rso_email', 'message': f'No RSO email found for hospital {center_id}.'}), 200
        
        current_out_values = content.get("outValues", [])
        hospital = content.get("hospitalName", "Unknown")
        month_key = content.get("month")
        data_type = content.get("dataType", "output")
        
        machine_settings = get_machine_settings(machine_id)
        tolerance_percent = machine_settings.get("tolerances", {}).get(data_type, {}).get("tolerance", 2.0)
        
        data_type_display = data_type.replace("_", " ").title()

        machine_doc = db.collection('linacs').document(machine_id).get()
        machine_name = machine_doc.to_dict().get('machineName', machine_id) if machine_doc.exists else machine_id

        month_alerts_doc_ref = db.collection("linac_alerts").document(machine_id).collection("months").document(f"Month_{month_key}_{data_type}")
        alerts_doc_snap = month_alerts_doc_ref.get()
        
        previously_alerted = alerts_doc_snap.to_dict().get("alerted_values", []) if alerts_doc_snap.exists else []
        previously_alerted_strings = set(json.dumps(val, sort_keys=True) for val in previously_alerted)
        current_out_values_strings = set(json.dumps(val, sort_keys=True) for val in current_out_values)

        if current_out_values_strings == previously_alerted_strings:
            return jsonify({'status': 'no_change', 'message': 'No new alerts or changes. Email not sent.'})

        subject = f"⚠ {data_type_display} QA Status - {hospital} ({machine_name}) - {month_key}"
        message_body = f"{data_type_display} QA Status Update for {hospital} (Machine: {machine_name}) for {month_key}\n\n"
        if current_out_values:
            message_body += f"Current Out-of-Tolerance Values (±{tolerance_percent}%):\n\n"
            for v in sorted(current_out_values, key=lambda x: (x.get('energy'), x.get('date'))):
                message_body += f"Energy: {v.get('energy', 'N/A')}, Date: {v.get('date', 'N/A')}, Value: {v.get('value', 'N/A')}%\n"
        else:
            message_body += f"All previously detected {data_type_display} QA issues for this machine and month are now resolved.\n"

        email_sent = send_notification_email(", ".join(recipient_emails), subject, message_body)

        if email_sent:
            month_alerts_doc_ref.set({"alerted_values": current_out_values}, merge=False)
            return jsonify({'status': 'alert sent'}), 200
        else:
            return jsonify({'status': 'email_send_error', 'message': 'Failed to send email.'}), 500

    except Exception as e:
        if SENTRY_DSN: sentry_sdk.capture_exception(e)
        return jsonify({'status': 'error', 'message': str(e)}), 500

# --- PREDICTION & FORECASTING ENDPOINTS ---
@app.route('/predictions', methods=['GET'])
def get_predictions():
    try:
        uid = request.args.get('uid')
        data_type = request.args.get('dataType')
        energy = request.args.get('energy')
        month = request.args.get('month')
        machine_id = request.args.get('machineId')

        if not all([uid, data_type, energy, month, machine_id]):
            return jsonify({'error': 'Missing required parameters'}), 400

        prediction_doc_id = f"{machine_id}_{data_type}_{energy}_{month}"
        prediction_doc = db.collection("linac_predictions").document(prediction_doc_id).get()

        if prediction_doc.exists:
            return jsonify(prediction_doc.to_dict()), 200
        else:
            return jsonify({'error': f'Prediction not found for {month}'}), 404
            
    except Exception as e:
        app.logger.error(f"Get predictions failed: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500

@app.route('/update-live-forecast', methods=['POST'])
def update_live_forecast():
    return jsonify({'status': 'error', 'message': 'Endpoint not yet updated for multi-machine support.'}), 501

@app.route('/historical-forecast', methods=['POST'])
def get_historical_forecast():
    try:
        token = request.headers.get("Authorization", "").split("Bearer ")[-1]
        is_valid, _, _ = verify_user_token(token)
        if not is_valid:
            return jsonify({'error': 'Unauthorized'}), 403

        content = request.get_json(force=True)
        month = content.get('month')
        data_type = content.get('dataType')
        energy = content.get('energy')
        machine_id = content.get('machineId')

        if not all([month, data_type, energy, machine_id]):
            return jsonify({'error': 'Missing required parameters'}), 400
        
        def fetch_historical_for_machine(m_id, dt, et, end_date_str):
            months_ref = db.collection("linac_data").document(m_id).collection("months").stream()
            all_vals = []
            
            for month_doc in months_ref:
                month_id_str = month_doc.id.replace("Month_", "")
                if month_id_str >= end_date_str: continue

                month_data = month_doc.to_dict()
                field_name = f"data_{dt}"
                if field_name in month_data:
                    year, mon = map(int, month_id_str.split("-"))
                    for row_data in month_data[field_name]:
                        if row_data.get("energy") == et:
                            for i, value in enumerate(row_data.get("values", [])):
                                day = i + 1
                                try:
                                    if value and day <= monthrange(year, mon)[1]:
                                        all_vals.append({"ds": pd.to_datetime(f"{year}-{mon}-{day}"), "y": float(value)})
                                except (ValueError, TypeError):
                                    continue
            return pd.DataFrame(all_vals)

        historical_df = fetch_historical_for_machine(machine_id, data_type, energy, month)
        
        if historical_df.empty or len(historical_df) < 10:
            return jsonify({'error': 'Not enough historical data to generate a forecast.'}), 400

        model = Prophet()
        model.fit(historical_df)
        
        year, mon = map(int, month.split('-'))
        _, num_days = monthrange(year, mon)
        
        future_dates = [pd.to_datetime(f"{year}-{mon}-{d}") for d in range(1, num_days + 1)]
        future_df = pd.DataFrame(future_dates, columns=['ds'])
        
        forecast = model.predict(future_df)

        actuals = [None] * num_days
        current_month_data_ref = db.collection("linac_data").document(machine_id).collection("months").document(f"Month_{month}").get()
        if current_month_data_ref.exists:
            data_field = current_month_data_ref.to_dict().get(f"data_{data_type}", [])
            energy_row = next((row for row in data_field if row.get("energy") == energy), None)
            if energy_row:
                for i, val in enumerate(energy_row.get("values", [])):
                    try: actuals[i] = float(val)
                    except (ValueError, TypeError): continue
        
        return jsonify({'forecast': json.loads(forecast.to_json(orient='records')), 'actuals': actuals})

    except Exception as e:
        app.logger.error(f"Error in historical forecast: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500


# --- NEW ENDPOINT FOR USERS TO FETCH THEIR MACHINES ---
@app.route('/user/machines', methods=['GET'])
def get_user_machines():
    """Gets all machines for the logged-in user's institution."""
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_valid, _, user_data = verify_user_token(token)
    
    if not is_valid or not user_data:
        return jsonify({'message': 'Unauthorized'}), 403

    center_id = user_data.get('centerId')
    if not center_id:
        return jsonify({'message': 'User is not associated with an institution.'}), 400

    try:
        machines_ref = db.collection('linacs').where('centerId', '==', center_id).stream()
        machines = [doc.to_dict() for doc in machines_ref]
        machines.sort(key=lambda x: x.get('machineName', ''))
        return jsonify(machines), 200
    except Exception as e:
        app.logger.error(f"Error getting user machines for {center_id}: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e)
        return jsonify({'message': str(e)}), 500

# --- DASHBOARD & CHATBOT FUNCTIONS ---
@app.route('/query-qa-data', methods=['POST'])
def query_qa_data():
    try:
        content = request.get_json(force=True)
        user_query_text = content.get("query_text", "").lower()

        with open('knowledge_base.json', 'r') as f:
            kb = json.load(f)

        if 'drift' in user_query_text or 'output' in user_query_text:
            topic = 'output_drift'
        elif 'flatness' in user_query_text or 'symmetry' in user_query_text:
            topic = 'flatness_warning'
        else:
            for keyword, path in kb.get("maintenance_info", {}).items():
                 if keyword.replace("_", " ") in user_query_text:
                     return jsonify({'status': 'success', 'message': path}), 200
            return jsonify({'status': 'error', 'message': "I can help diagnose issues with 'output drift' or 'flatness'. What would you like to diagnose?"}), 404

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
# --- EVENT LOGGING ENDPOINT ---
@app.route('/log_event', methods=['POST'])
def log_event():
    try:
        content = request.get_json(force=True)
        action = content.get("action")
        user_uid = content.get("userUid")

        if not action or not user_uid:
            return jsonify({'status': 'error', 'message': 'Missing action or userUid'}), 400

        user_doc = db.collection('users').document(user_uid).get()
        user_data = user_doc.to_dict() if user_doc.exists else {}
        
        audit_entry = {
            "timestamp": firestore.SERVER_TIMESTAMP,
            "action": action, # e.g., 'user_logout'
            "targetUserUid": user_uid,
            "hospital": user_data.get("hospital", "N/A"),
            "details": {
                "user_email": user_data.get("email", "N/A"),
                "user_agent": request.headers.get('User-Agent')
            }
        }
        db.collection("audit_logs").add(audit_entry)
        
        return jsonify({'status': 'success', 'message': 'Event logged'}), 200
    except Exception as e:
        app.logger.error(f"Error logging event: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e)
        return jsonify({'status': 'error', 'message': str(e)}), 500
# --- SUPER ADMIN ENDPOINTS ---
def verify_super_admin_token(id_token):
    """Verifies the token belongs to a Super Admin."""
    try:
        decoded_token = auth.verify_id_token(id_token)
        uid = decoded_token['uid']
        user_doc = db.collection('users').document(uid).get()
        if user_doc.exists and user_doc.to_dict().get('role') == 'Super Admin':
            return True, uid
    except Exception as e:
        app.logger.error(f"Super Admin Token verification failed: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e)
    return False, None
@app.route('/superadmin/institutions', methods=['GET'])
def get_institutions():
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_super_admin, _ = verify_super_admin_token(token)
    if not is_super_admin:
        return jsonify({'message': 'Unauthorized: Super Admin access required'}), 403
    
    try:
        institutions_ref = db.collection('institutions').order_by("name").stream()
        institutions = [doc.to_dict() for doc in institutions_ref]
        return jsonify(institutions), 200
    except Exception as e:
        app.logger.error(f"Error getting institutions: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e)
        return jsonify({'message': str(e)}), 500
@app.route('/superadmin/institutions', methods=['POST'])
def add_institution():
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_super_admin, _ = verify_super_admin_token(token)
    if not is_super_admin:
        return jsonify({'message': 'Unauthorized: Super Admin access required'}), 403
    
    try:
        content = request.get_json(force=True)
        name = content.get('name')
        center_id = content.get('centerId')
        parent_group = content.get('parentGroup')
        if not all([name, center_id, parent_group]):
            return jsonify({'message': 'Missing name, centerId, or parentGroup'}), 400

        institution_ref = db.collection('institutions').document(center_id)
        if institution_ref.get().exists:
            return jsonify({'message': 'Institution with this centerId already exists'}), 409
        
        institution_ref.set({
            'name': name,
            'centerId': center_id,
            'parentGroup': parent_group,
            'createdAt': firestore.SERVER_TIMESTAMP
        })
        return jsonify({'status': 'success', 'message': 'Institution added successfully'}), 201
    except Exception as e:
        app.logger.error(f"Error adding institution: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e)
        return jsonify({'message': str(e)}), 500

# --- NEW ENDPOINT TO DELETE AN INSTITUTION ---
@app.route('/superadmin/institution/<center_id>', methods=['DELETE'])
def delete_institution(center_id):
    """Deletes an institution. NOTE: This does not delete associated machines or users."""
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_super_admin, _ = verify_super_admin_token(token)
    if not is_super_admin:
        return jsonify({'message': 'Unauthorized'}), 403
    
    try:
        db.collection('institutions').document(center_id).delete()
        # NOTE: For a production system, you might want to handle orphaned users/machines
        # in a more sophisticated way (e.g., a cleanup script or archiving).
        return jsonify({'status': 'success', 'message': 'Institution deleted successfully'}), 200
    except Exception as e:
        app.logger.error(f"Error deleting institution {center_id}: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e)
        return jsonify({'message': str(e)}), 500

@app.route('/superadmin/create-admin', methods=['POST'])
def create_admin_user():
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_super_admin, super_admin_uid = verify_super_admin_token(token)
    if not is_super_admin:
        return jsonify({'message': 'Unauthorized: Super Admin access required'}), 403

    try:
        content = request.get_json(force=True)
        email = content.get('email')
        password = content.get('password')
        name = content.get('name')
        manages_group = content.get('managesGroup')

        if not all([email, password, name, manages_group]):
            return jsonify({'message': 'Missing required fields for creating an admin'}), 400

        new_user = auth.create_user(email=email, password=password, display_name=name)

        user_ref = db.collection('users').document(new_user.uid)
        user_ref.set({
            'name': name,
            'email': email,
            'role': 'Admin',
            'status': 'active',
            'managesGroup': manages_group
        })

        audit_entry = {
            "timestamp": firestore.SERVER_TIMESTAMP,
            "adminUid": super_admin_uid,
            "action": "superadmin_create_admin",
            "targetUserUid": new_user.uid,
            "details": {
                "created_user_email": email,
                "assigned_group": manages_group
            }
        }
        db.collection("audit_logs").add(audit_entry)

        return jsonify({'status': 'success', 'message': f'Admin user {email} created successfully.'}), 201

    except auth.EmailAlreadyExistsError:
        return jsonify({'message': 'This email address is already in use.'}), 409
    except Exception as e:
        app.logger.error(f"Error creating admin user: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e)
        if 'new_user' in locals() and new_user.uid:
            try:
                auth.delete_user(new_user.uid)
                app.logger.warning(f"Cleaned up orphaned auth user {new_user.uid} after creation failure.")
            except Exception as cleanup_error:
                app.logger.error(f"Failed to clean up orphaned auth user {new_user.uid}: {cleanup_error}")
        return jsonify({'message': str(e)}), 500

# --- MODIFIED MACHINE MANAGEMENT ENDPOINTS (NOW FOR ADMINS) ---
@app.route('/admin/machines', methods=['POST'])
def add_machines():
    """Adds one or more new LINAC machines to an institution."""
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_admin, _, _ = verify_admin_token(token)
    if not is_admin:
        return jsonify({'message': 'Unauthorized'}), 403
    
    try:
        content = request.get_json(force=True)
        center_id = content.get('centerId')
        machine_names = content.get('machines')

        if not center_id or not machine_names or not isinstance(machine_names, list):
            return jsonify({'message': 'centerId and a list of machine names are required'}), 400

        batch = db.batch()
        for name in machine_names:
            if not name.strip(): continue

            existing_machine = db.collection('linacs').where('centerId', '==', center_id).where('machineName', '==', name).limit(1).get()
            if len(existing_machine) > 0:
                return jsonify({'message': f'A machine with name "{name}" already exists for this institution.'}), 409
                
            machine_id = str(uuid.uuid4())
            machine_ref = db.collection('linacs').document(machine_id)
            batch.set(machine_ref, {
                'machineId': machine_id,
                'machineName': name,
                'centerId': center_id,
                'createdAt': firestore.SERVER_TIMESTAMP
            })
        batch.commit()
        
        return jsonify({'status': 'success', 'message': f'{len(machine_names)} machine(s) added successfully to {center_id}.'}), 201

    except Exception as e:
        app.logger.error(f"Error adding machines: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e)
        return jsonify({'message': str(e)}), 500
@app.route('/admin/machines', methods=['GET'])
def get_machines_for_institution():
    """Gets all machines for a specific institution."""
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_admin, _, _ = verify_admin_token(token)
    if not is_admin:
        return jsonify({'message': 'Unauthorized'}), 403
            
    center_id = request.args.get('centerId')
    if not center_id:
        return jsonify({'message': 'centerId query parameter is required'}), 400

    try:
        machines_ref = db.collection('linacs').where('centerId', '==', center_id).stream()
        machines = [doc.to_dict() for doc in machines_ref]
        machines.sort(key=lambda x: x.get('machineName', ''))
        return jsonify(machines), 200
    except Exception as e:
        app.logger.error(f"Error getting machines for {center_id}: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e)
        return jsonify({'message': str(e)}), 500
@app.route('/admin/machine/<machine_id>', methods=['PUT'])
def update_machine(machine_id):
    """Updates a machine's name."""
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_admin, _, _ = verify_admin_token(token)
    if not is_admin:
        return jsonify({'message': 'Unauthorized'}), 403
    
    try:
        content = request.get_json(force=True)
        new_name = content.get('machineName')
        if not new_name:
            return jsonify({'message': 'New machineName is required'}), 400
            
        machine_ref = db.collection('linacs').document(machine_id)
        machine_ref.update({'machineName': new_name})
        return jsonify({'status': 'success', 'message': 'Machine updated successfully'}), 200
    except Exception as e:
        app.logger.error(f"Error updating machine {machine_id}: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e)
        return jsonify({'message': str(e)}), 500
@app.route('/admin/machine/<machine_id>', methods=['DELETE'])
def delete_machine(machine_id):
    """Deletes a machine record."""
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_admin, _, _ = verify_admin_token(token)
    if not is_admin:
        return jsonify({'message': 'Unauthorized'}), 403
        
    try:
        db.collection('linacs').document(machine_id).delete()
        return jsonify({'status': 'success', 'message': 'Machine deleted successfully'}), 200
    except Exception as e:
        app.logger.error(f"Error deleting machine {machine_id}: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e)
        return jsonify({'message': str(e)}), 500

# --- MODIFIED ADMIN ANALYSIS ENDPOINTS ---
def calculate_machine_metrics(machine_id, period_days=90):
    all_numeric_values = {dtype: [] for dtype in DATA_TYPES}
    warnings = 0
    oots = 0
    start_date = datetime.now() - timedelta(days=period_days)
    
    machine_settings = get_machine_settings(machine_id)
    tolerances_for_machine = machine_settings.get("tolerances", DEFAULT_TOLERANCES)
    
    months_ref = db.collection("linac_data").document(machine_id).collection("months").stream()

    for month_doc in months_ref:
        month_id_str = month_doc.id.replace("Month_", "")
        try:
            month_dt = datetime.strptime(month_id_str, '%Y-%m')
            if month_dt.year < start_date.year or (month_dt.year == start_date.year and month_dt.month < start_date.month):
                continue
        except ValueError:
            continue

        month_data = month_doc.to_dict()
        for data_type, config in tolerances_for_machine.items():
            field_name = f"data_{data_type}"
            if field_name in month_data:
                for row in month_data[field_name]:
                    for value in row.get("values", []):
                        try:
                            val = float(value)
                            all_numeric_values[data_type].append(val)
                            abs_val = abs(val)
                            if abs_val > config["tolerance"]:
                                oots += 1
                            elif abs_val >= config["warning"]:
                                warnings += 1
                        except (ValueError, TypeError):
                            continue
    
    machine_doc = db.collection('linacs').document(machine_id).get()
    machine_name = machine_id
    hospital_name = "Unknown"
    if machine_doc.exists:
        machine_data = machine_doc.to_dict()
        machine_name = machine_data.get("machineName", machine_id)
        hospital_name = machine_data.get("centerId", "Unknown")


    results = {
        "machineId": machine_id,
        "machineName": machine_name,
        "hospital": hospital_name,
        "warnings": warnings,
        "oots": oots,
        "metrics": {}
    }
    for data_type, values in all_numeric_values.items():
        if values:
            results["metrics"][data_type] = {
                "mean_deviation": np.nanmean(values),
                "std_deviation": np.nanstd(values),
                "data_points": len(values)
            }
        else:
            results["metrics"][data_type] = { "mean_deviation": 0, "std_deviation": 0, "data_points": 0 }
            
    return results

@app.route('/admin/benchmark-metrics', methods=['GET'])
def get_benchmark_metrics():
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_admin, _, admin_data = verify_admin_token(token)
    if not is_admin:
        return jsonify({'message': 'Unauthorized'}), 403

    try:
        period = int(request.args.get('period', 90))
        hospital_filter = request.args.get('hospitalId')
        admin_role = admin_data.get('role')
        
        visible_machines_query = db.collection('linacs')
        
        if hospital_filter:
             visible_machines_query = visible_machines_query.where('centerId', '==', hospital_filter)
        
        elif admin_role == 'Admin':
            admin_group = admin_data.get('managesGroup')
            if not admin_group: return jsonify([])
            
            hospitals_ref = db.collection('institutions').where('parentGroup', '==', admin_group).stream()
            hospital_ids = [inst.id for inst in hospitals_ref]
            
            if not hospital_ids: return jsonify([])
            
            if len(hospital_ids) > 30:
                hospital_ids = hospital_ids[:30]

            visible_machines_query = visible_machines_query.where('centerId', 'in', hospital_ids)

        all_machines = visible_machines_query.stream()
        
        benchmark_data = []
        for machine in all_machines:
            metrics = calculate_machine_metrics(machine.id, period_days=period)
            benchmark_data.append(metrics)
        
        benchmark_data.sort(key=lambda x: (x['oots'], x['warnings']), reverse=True)

        return jsonify(benchmark_data), 200

    except Exception as e:
        app.logger.error(f"Error getting benchmark metrics: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e)
        return jsonify({'message': str(e)}), 500
        
# --- MODIFIED CORRELATION ANALYSIS (MACHINE-AWARE) ---
@app.route('/admin/correlation-analysis', methods=['GET'])
def get_correlation_analysis():
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_admin, _, _ = verify_admin_token(token)
    if not is_admin:
        return jsonify({'message': 'Unauthorized'}), 403

    try:
        machine_id = request.args.get('machineId')
        data_type = request.args.get('dataType')
        energy = request.args.get('energy')

        if not all([machine_id, data_type, energy]):
            return jsonify({'error': 'Missing required parameters'}), 400

        months_ref = db.collection("linac_data").document(machine_id).collection("months").stream()
        qa_values = []
        for month_doc in months_ref:
            month_data = month_doc.to_dict()
            field_name = f"data_{data_type}"
            if field_name in month_data:
                month_id_str = month_doc.id.replace("Month_", "")
                year, mon = map(int, month_id_str.split("-"))
                for row_data in month_data[field_name]:
                    if row_data.get("energy") == energy:
                        for i, value in enumerate(row_data.get("values", [])):
                            day = i + 1
                            try:
                                if value and day <= monthrange(year, mon)[1]:
                                    date_str = f"{year}-{mon:02d}-{day:02d}"
                                    qa_values.append({"date": date_str, "qa_value": float(value)})
                            except (ValueError, TypeError):
                                continue
        
        if not qa_values:
            return jsonify({'error': 'No QA data found for the selected criteria.'}), 404

        qa_df = pd.DataFrame(qa_values)
        qa_df['date'] = pd.to_datetime(qa_df['date'])

        env_docs = db.collection("linac_data").document(machine_id).collection("daily_env").stream()
        env_values = []
        for doc in env_docs:
            data = doc.to_dict()
            data['date'] = doc.id
            env_values.append(data)
        
        if not env_values:
            return jsonify({'error': 'No environmental data found for this machine.'}), 404
        
        env_df = pd.DataFrame(env_values)
        env_df['date'] = pd.to_datetime(env_df['date'])
        
        merged_df = pd.merge(qa_df, env_df, on='date', how='inner').dropna()

        if len(merged_df) < 5: 
            return jsonify({'error': f'Not enough overlapping data points found ({len(merged_df)}).'}), 404
        
        results = {}
        env_factors = ['temperature_celsius', 'pressure_hpa']

        for factor in env_factors:
            if factor in merged_df and len(merged_df[factor].unique()) > 1:
                corr, p_value = stats.pearsonr(merged_df['qa_value'], merged_df[factor])
                if np.isnan(corr): 
                    corr = 0.0
                    p_value = 1.0
                results[factor] = {'correlation': corr, 'p_value': p_value}

        return jsonify(results), 200

    except Exception as e:
        app.logger.error(f"Error in correlation analysis: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e)
        return jsonify({'message': str(e)}), 500

@app.route('/admin/users', methods=['GET'])
def get_all_users():
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_admin, _, admin_data = verify_admin_token(token)
    if not is_admin:
        return jsonify({'message': 'Unauthorized'}), 403
    try:
        users_query = db.collection("users")
        
        admin_role = admin_data.get('role')
        if admin_role == 'Admin':
            admin_group = admin_data.get('managesGroup')
            if not admin_group: return jsonify([])
            users_query = users_query.where('parentGroup', '==', admin_group)
        
        users_stream = users_query.stream()
        return jsonify([doc.to_dict() | {"uid": doc.id} for doc in users_stream]), 200
    except Exception as e:
        app.logger.error(f"Get all users failed: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e)
        return jsonify({'message': str(e)}), 500

@app.route('/admin/update-user-status', methods=['POST'])
def update_user_status():
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_admin, admin_uid_from_token, _ = verify_admin_token(token)
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
            "hospital": old_user_data.get("hospital", "N/A")
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
    is_admin, admin_uid_from_token, _ = verify_admin_token(token)
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
@app.route('/admin/hospital-data', methods=['GET'])
def get_hospital_data():
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_admin, _, _ = verify_admin_token(token)
    if not is_admin:
        return jsonify({'message': 'Unauthorized'}), 403

    try:
        machine_id = request.args.get('machineId')
        month_param = request.args.get('month')
        if not machine_id or not month_param:
            return jsonify({'error': 'Missing machineId or month parameter'}), 400

        year, mon = map(int, month_param.split("-"))
        _, num_days = monthrange(year, mon)
        
        machine_settings = get_machine_settings(machine_id)
        energy_types_for_machine = machine_settings.get("energyTypes", DEFAULT_ENERGY_TYPES)
        
        all_data = {}
        for data_type in DATA_TYPES:
            doc_ref = db.collection("linac_data").document(machine_id).collection("months").document(f"Month_{month_param}")
            doc = doc_ref.get()
            
            energy_dict = {e: [""] * num_days for e in energy_types_for_machine}
            if doc.exists:
                firestore_field_name = f"data_{data_type}"
                doc_data = doc.to_dict().get(firestore_field_name, [])
                for row in doc_data:
                    energy, values = row.get("energy"), row.get("values", [])
                    if energy in energy_dict:
                        energy_dict[energy] = (values + [""] * num_days)[:num_days]

            table = [[e] + energy_dict[e] for e in energy_types_for_machine]
            all_data[data_type] = table

        return jsonify({'data': all_data}), 200

    except Exception as e:
        app.logger.error(f"Admin get hospital data failed: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500

@app.route('/admin/audit-logs', methods=['GET'])
def get_audit_logs():
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_admin, _, admin_data = verify_admin_token(token)
    if not is_admin:
        return jsonify({'message': 'Unauthorized'}), 403

    try:
        logs_query = db.collection("audit_logs").order_by("timestamp", direction=firestore.Query.DESCENDING)
        
        admin_group = admin_data.get('managesGroup')
        
        # Super Admins can see all logs
        if admin_data.get('role') == 'Super Admin':
            hospital_id = request.args.get('hospitalId')
            if hospital_id:
                logs_query = logs_query.where('hospital', '==', hospital_id)
        # Regular Admins can only see logs for hospitals in their group
        elif admin_group:
            hospitals_in_group_ref = db.collection('institutions').where('parentGroup', '==', admin_group).stream()
            hospital_ids = [inst.id for inst in hospitals_in_group_ref]
            
            requested_hospital_id = request.args.get('hospitalId')
            
            # If the user tries to filter by a specific hospital, ensure it's in their group
            if requested_hospital_id:
                if requested_hospital_id in hospital_ids:
                    logs_query = logs_query.where('hospital', '==', requested_hospital_id)
                else:
                    return jsonify({'message': 'Unauthorized: Cannot access logs for this hospital.'}), 403
            else:
                # If no specific hospital is requested, show all logs for their group's hospitals
                if hospital_ids:
                    # Firestore 'in' query supports up to 10 values without a composite index.
                    # For more than 10, a composite index on `hospital` and `timestamp` would be needed.
                    logs_query = logs_query.where('hospital', 'in', hospital_ids)
                else:
                    return jsonify({"logs": []}), 200

        # Apply common filters (action and date)
        action = request.args.get('action')
        date_str = request.args.get('date')

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
                        user_data = user_doc.to_dict()
                        user_name = user_data.get('name', user_uid)
                        user_email = user_data.get('email', '')
                        user_hospital = user_data.get('hospital', 'N/A')
                        display_string = f"{user_name} ({user_email})\n{user_hospital}"
                        log_data['user_display'] = display_string
                        user_cache[user_uid] = display_string
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
        
# --- MODIFIED SERVICE IMPACT ANALYSIS (MACHINE-AWARE) ---
def fetch_data_for_period(machine_id, start_date, end_date):
    """
    Fetches all 'output' data for a specific machine within a date range.
    """
    data_points = {}
    
    months_to_check = set()
    current_date = start_date
    while current_date <= end_date:
        months_to_check.add(current_date.strftime("Month_%Y-%m"))
        current_date += timedelta(days=1)
    
    for month_doc_id in months_to_check:
        month_doc = db.collection("linac_data").document(machine_id).collection("months").document(month_doc_id).get()
        if not month_doc.exists:
            continue
        
        month_data = month_doc.to_dict().get("data_output", [])
        month_str = month_doc_id.replace("Month_", "")
        
        for row in month_data:
            energy = row.get("energy")
            if energy not in data_points:
                data_points[energy] = []
                
            for i, value in enumerate(row.get("values", [])):
                day = i + 1
                try:
                    current_point_date = datetime.strptime(f"{month_str}-{day}", "%Y-%m-%d")
                    if start_date <= current_point_date <= end_date:
                        if value not in [None, '']:
                            data_points[energy].append(float(value))
                except (ValueError, TypeError):
                    continue
                    
    return data_points
    
@app.route('/admin/service-impact-analysis', methods=['GET'])
def get_service_impact_analysis():
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_admin, _, _ = verify_admin_token(token)
    if not is_admin:
        return jsonify({'message': 'Unauthorized'}), 403

    machine_id = request.args.get('machineId')
    if not machine_id:
        return jsonify({'message': 'machineId is required'}), 400

    try:
        service_events_ref = db.collection('linac_data').document(machine_id).collection('service_events')
        service_events = service_events_ref.stream()
        
        analysis_results = []

        for event in service_events:
            service_date = datetime.strptime(event.id, "%Y-%m-%d")
            
            before_start = service_date - timedelta(days=14)
            before_end = service_date - timedelta(days=1)
            after_start = service_date + timedelta(days=1)
            after_end = service_date + timedelta(days=14)
            
            before_data_by_energy = fetch_data_for_period(machine_id, before_start, before_end)
            after_data_by_energy = fetch_data_for_period(machine_id, after_start, after_end)

            processed_energies = set(before_data_by_energy.keys()) | set(after_data_by_energy.keys())

            for energy in processed_energies:
                before_values = before_data_by_energy.get(energy, [])
                after_values = after_data_by_energy.get(energy, [])
                
                if not before_values or not after_values:
                    continue

                before_metrics = { "mean_deviation": np.mean(before_values), "std_deviation": np.std(before_values) }
                after_metrics = { "mean_deviation": np.mean(after_values), "std_deviation": np.std(after_values) }
                
                improvement = 0
                if before_metrics["std_deviation"] > 0:
                    improvement = ((before_metrics["std_deviation"] - after_metrics["std_deviation"]) / before_metrics["std_deviation"]) * 100

                analysis_results.append({
                    "service_date": event.id,
                    "energy": energy,
                    "before_metrics": before_metrics,
                    "after_metrics": after_metrics,
                    "stability_improvement_percent": improvement,
                    "before_data": before_values,
                    "after_data": after_values
                })

        analysis_results.sort(key=lambda x: x['service_date'], reverse=True)
        
        return jsonify(analysis_results), 200

    except Exception as e:
        app.logger.error(f"Error in service impact analysis: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e)
        return jsonify({'message': str(e)}), 500

# --- INDEX AND RUN ---
@app.route('/')
def index():
    return "✅ LINAC QA Backend Running"

if __name__ == '__main__':
    app.run(debug=True)

