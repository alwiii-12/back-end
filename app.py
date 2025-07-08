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
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import json
import logging
from calendar import monthrange
from datetime import datetime, timedelta

import firebase_admin
from firebase_admin import credentials, firestore, auth

# New imports for Excel export
import pandas as pd
from io import BytesIO


app = Flask(__name__)

# Explicitly configure CORS to allow your frontend origin
# IMPORTANT: Replace 'https://front-endnew.onrender.com' with your actual deployed frontend URL.
# For development, you might use "http://localhost:XXXX" or origins="*".
# For production, specify your exact frontend domain(s).
CORS(app, resources={r"/*": {"origins": "https://front-endnew.onrender.com"}})

app.logger.setLevel(logging.DEBUG)

# --- [EMAIL CONFIG] ---
SENDER_EMAIL = os.environ.get('SENDER_EMAIL', 'itsmealwin12@gmail.com')
RECEIVER_EMAIL = os.environ.get('RECEIVER_EMAIL', 'alwinjose812@gmail.com') # This might be less used now
APP_PASSWORD = os.environ.get('EMAIL_APP_PASSWORD')

# --- [EMAIL SENDER FUNCTION] ---
# Consolidated email sending logic
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

# Check if a default app is already initialized before initializing
if not firebase_admin._apps:
    cred = credentials.Certificate(firebase_dict)
    firebase_admin.initialize_app(cred)
    app.logger.info("Firebase default app initialized.")
else:
    app.logger.info("Firebase default app already initialized, skipping init.")

db = firestore.client()

# Defined once here for consistency
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
        app.logger.error(f"Token verification failed: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e) # Capture token verification failures
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
        app.logger.error(f"Signup failed: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e) # Capture signup errors
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
        app.logger.error(f"Login failed: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e) # Capture login errors
        return jsonify({'status': 'error', 'message': 'Login failed'}), 500

# --- NEW: Generic Log Event Endpoint ---
@app.route('/log_event', methods=['POST', 'OPTIONS']) # ADDED 'OPTIONS' METHOD HERE
def log_event():
    # For OPTIONS requests (preflight), Flask-CORS handles it automatically.
    # No custom logic is usually needed here for OPTIONS.
    if request.method == 'OPTIONS':
        return '', 200

    try:
        event_data = request.get_json(force=True)
        
        # Ensure minimum required fields for an audit log
        if not event_data.get("action") or not event_data.get("userUid"):
            app.logger.warning("Attempted to log event with missing action or userUid.")
            return jsonify({'status': 'error', 'message': 'Missing action or userUid'}), 400

        # Add server timestamp if not provided (frontend usually won't send it)
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
        
        # Before saving, get current data from DB to determine what's "new"
        existing_doc = db.collection("linac_data").document(center_id).collection("months").document(month_doc_id).get()
        existing_data_map = {}
        if existing_doc.exists:
            for row in existing_doc.to_dict().get("data", []):
                existing_data_map[row.get("energy")] = row.get("values", [])

        db.collection("linac_data").document(center_id).collection("months").document(month_doc_id).set(
            {"data": converted}, merge=True)
        
        # --- Anomaly Detection on newly changed or added data points (PLACEHOLDER) ---
        anomalies_detected = []
        
        if anomalies_detected:
            app.logger.info(f"Anomalies detected during save: {anomalies_detected}")

        return jsonify({'status': 'success', 'anomalies': anomalies_detected}), 200
    except Exception as e:
        app.logger.error(f"Save data failed: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e) # Capture save data errors
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
                    energy_dict[energy] = (values + [""] * num_days)[:num_days]

        table = [[e] + energy_dict[e] for e in ENERGY_TYPES]
        return jsonify({'data': table}), 200
    except Exception as e:
        app.logger.error(f"Get data failed: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e) # Capture get data errors
        return jsonify({'error': str(e)}), 500

# --- ALERT EMAIL ---
@app.route('/send-alert', methods=['POST'])
async def send_alert():
    try:
        content = request.get_json(force=True)
        current_out_values = content.get("outValues", [])
        hospital = content.get("hospitalName", "Unknown")
        uid = content.get("uid")
        month_key = content.get("month")

        if not uid or not month_key:
            app.logger.warning("Missing UID or month key for alert processing.")
            if sentry_sdk_configured:
                sentry_sdk.capture_message("Missing UID or month key for alert processing.", level="warning")
            return jsonify({'status': 'error', 'message': 'Missing UID or month for alert processing'}), 400

        user_doc = db.collection('users').document(uid).get()
        if not user_doc.exists:
            app.logger.warning(f"User document not found for UID: {uid} during alert processing.")
            if sentry_sdk_configured:
                sentry_sdk.capture_message(f"User document not found for UID: {uid} during alert processing.", level="warning")
            return jsonify({'status': 'error', 'message': 'User not found for alert processing'}), 404
        user_data = user_doc.to_dict()
        center_id = user_data.get('centerId')

        if not center_id:
            app.logger.warning(f"Center ID not found for user {uid} during alert processing.")
            if sentry_sdk_configured:
                sentry_sdk.capture_message(f"Center ID not found for user {uid} during alert processing.", level="warning")
            return jsonify({'status': 'error', 'message': 'Center ID not found for user for alert processing'}), 400

        rso_emails = []
        try:
            rso_users = db.collection('users').where('centerId', '==', center_id).where('role', '==', 'RSO').stream()
            # UserWarning: Detected filter using positional arguments. Prefer using the 'filter' keyword argument instead.
            # Fix: rso_users = db.collection('users').filter(firestore.FieldFilter('centerId', '==', center_id)).filter(firestore.FieldFilter('role', '==', 'RSO')).stream()
            for rso_user in rso_users:
                rso_data = rso_user.to_dict()
                if 'email' in rso_data and rso_data['email']:
                    rso_emails.append(rso_data['email'])
            
            if not rso_emails:
                app.logger.info(f"No RSO email found for centerId: {center_id}. Alert not sent to RSO.")
                if sentry_sdk_configured:
                    sentry_sdk.capture_message(f"No RSO email found for centerId: {center_id}. Alert not sent.", level="info")
                return jsonify({'status': 'no_rso_email', 'message': 'No RSO email found for this hospital.'}), 200

        except Exception as e:
            app.logger.error(f"Error fetching RSO emails for center {center_id}: {str(e)}", exc_info=True)
            if sentry_sdk_configured:
                sentry_sdk.capture_exception(e)
            return jsonify({'status': 'error', 'message': 'Failed to fetch RSO emails'}), 500

        if not APP_PASSWORD:
            app.logger.warning("APP_PASSWORD not configured. Cannot send email.")
            if sentry_sdk_configured:
                sentry_sdk.capture_message("APP_PASSWORD not configured. Cannot send alert email.", level="warning")
            return jsonify({'status': 'email_credentials_missing', 'message': 'Email credentials missing'}), 500

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
        
        email_sent = send_notification_email(", ".join(rso_emails), f"⚠ LINAC QA Status - {hospital} ({month_key})", message_body)

        if email_sent:
            month_alerts_doc_ref.set({"alerted_values": current_out_values}, merge=False)
            app.logger.debug(f"Alert state updated in Firestore for {center_id}/{month_key}.")
            return jsonify({'status': 'alert sent', 'message': 'Email sent and alert state updated.'}), 200
        else:
            app.logger.error("Failed to send alert email via helper function.")
            if sentry_sdk_configured:
                sentry_sdk.capture_message("Failed to send alert email via helper function.", level="error")
            return jsonify({'status': 'email_send_error', 'message': 'Failed to send email via helper function.'}), 500

    except Exception as e:
        app.logger.error(f"Error in send_alert function: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e)
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
        date_param = content.get("date")

        if not query_type or not month_param or not uid:
            return jsonify({'status': 'error', 'message': 'Missing query type, month, or UID'}), 400

        user_doc = db.collection("users").document(uid).get()
        if not user_doc.exists:
            return jsonify({'status': 'error', 'message': 'User not found'}), 404
        user_data = user_doc.to_dict()
        center_id = user_data.get("centerId")

        if not center_id:
            return jsonify({'status': 'error', 'message': 'Missing centerId for user'}), 400

        # Fetch data from Firestore for the specified month and user
        doc = db.collection("linac_data").document(center_id).collection("months").document(f"Month_{month_param}").get()
        data_rows = []
        if doc.exists:
            data_rows = doc.to_dict().get("data", [])

        # --- Query Logic ---
        if query_type == "out_of_tolerance_dates":
            year, mon = map(int, month_param.split("-"))
            _, num_days = monthrange(year, mon)
            date_strings = [f"{year}-{str(mon).zfill(2)}-{str(i+1).zfill(2)}" for i in range(num_days)]
            
            out_dates = set()
            for row in data_rows:
                energy_type_row = row.get("energy")
                values = row.get("values", [])
                for i, value in enumerate(values):
                    try:
                        n = float(value)
                        if abs(n) > 2.0: # Greater than 2.0% implies 'out of tolerance'
                                if i < len(date_strings):
                                    out_dates.add(date_strings[i])
                    except (ValueError, TypeError):
                        pass
            
            sorted_out_dates = sorted(list(out_dates))

            return jsonify({'status': 'success', 'dates': sorted_out_dates}), 200

        elif query_type == "energy_data_for_month":
            if not energy_type:
                return jsonify({'status': 'error', 'message': 'Missing energy_type for this query'}), 400
            
            found_row = None
            for row in data_rows:
                if row.get("energy") == energy_type:
                    found_row = row
                    break
            
            if found_row:
                year, mon = map(int, month_param.split("-"))
                _, num_days = monthrange(year, mon)
                dates = [f"{year}-{str(mon).zfill(2)}-{str(i+1).zfill(2)}" for i in range(num_days)]
                
                # Format data as a list of dictionaries for easier consumption
                formatted_data = []
                values = found_row.get("values", [])
                for i, val in enumerate(values):
                    if i < len(dates):
                        formatted_data.append({"date": dates[i], "value": val})

                return jsonify({'status': 'success', 'energy_type': energy_type, 'data': formatted_data}), 200
            else:
                return jsonify({'status': 'success', 'energy_type': energy_type, 'data': [], 'message': f"No data found for {energy_type} this month."}), 200

        elif query_type == "value_on_date":
            if not energy_type or not date_param:
                return jsonify({'status': 'error', 'message': 'Missing energy_type or date for this query'}), 400

            # Validate and parse date_param to get the day index
            try:
                parsed_date_obj = datetime.strptime(date_param, "%Y-%m-%d")
                if parsed_date_obj.year != int(month_param.split('-')[0]) or parsed_date_obj.month != int(month_param.split('-')[1]):
                    return jsonify({'status': 'error', 'message': 'Date provided does not match the current month/year.'}), 400
                
                day_index = parsed_date_obj.day - 1 # Convert day (1-based) to index (0-based)

            except ValueError:
                app.logger.error(f"Invalid date format in /query-qa-data: {date_param}", exc_info=True)
                if sentry_sdk_configured:
                    sentry_sdk.capture_message(f"Invalid date format in /query-qa-data: {date_param}", level="warning")
                return jsonify({'status': 'error', 'message': 'Invalid date format. Please useYYYY-MM-DD.'}), 400


            found_value = None
            found_status = "N/A"

            for row in data_rows:
                if row.get("energy") == energy_type:
                    values = row.get("values", [])
                    if day_index < len(values):
                        found_value = values[day_index]
                        try:
                            n = float(found_value)
                            if abs(n) <= 1.8:
                                found_status = "Within Tolerance"
                            elif abs(n) <= 2.0:
                                found_status = "Warning"
                            else:
                                found_status = "Out of Tolerance"
                        except (ValueError, TypeError):
                            found_status = "Not a number"
                    break
            
            if found_value is not None:
                return jsonify({
                    'status': 'success',
                    'energy_type': energy_type,
                    'date': date_param,
                    'value': found_value,
                    'data_status': found_status
                }), 200
            else:
                return jsonify({'status': 'success', 'message': f"No data found for {energy_type} on {date_param}."}), 200

        elif query_type == "warning_values_for_month":
            year, mon = map(int, month_param.split("-"))
            _, num_days = monthrange(year, mon)
            date_strings = [f"{year}-{str(mon).zfill(2)}-{str(i+1).zfill(2)}" for i in range(num_days)]
            
            warning_entries = []
            for row in data_rows:
                energy_type_row = row.get("energy")
                values = row.get("values", [])
                for i, value in enumerate(values):
                    try:
                        n = float(value)
                        if abs(n) > 1.8 and abs(n) <= 2.0:
                            if i < len(date_strings):
                                warning_entries.append({
                                    "energy": energy_type_row,
                                    "date": date_strings[i],
                                    "value": n
                                })
                    except (ValueError, TypeError):
                        pass
            
            sorted_warning_entries = sorted(warning_entries, key=lambda x: (x['date'], x['energy']))

            return jsonify({'status': 'success', 'warning_entries': sorted_warning_entries}), 200


        else:
            return jsonify({'status': 'error', 'message': 'Unknown query type'}), 400

    except Exception as e:
        app.logger.error(f"Chatbot query failed: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e)
        return jsonify({'status': 'error', 'message': str(e)}), 500

# --- ADMIN: GET PENDING USERS ---
@app.route('/admin/pending-users', methods=['GET'])
async def get_pending_users():
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_admin, _ = await verify_admin_token(token)
    if not is_admin:
        return jsonify({'message': 'Unauthorized'}), 403
    try:
        users = db.collection("users").where(filter=firestore.FieldFilter("status", "==", "pending")).stream() # Corrected filter usage
        return jsonify([doc.to_dict() | {"uid": doc.id} for doc in users]), 200
    except Exception as e:
        app.logger.error(f"Get pending users failed: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e)
        return jsonify({'message': str(e)}), 500

# --- ADMIN: GET ALL USERS (with optional filters) ---
@app.route('/admin/users', methods=['GET'])
async def get_all_users():
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_admin, _ = await verify_admin_token(token)
    if not is_admin:
        return jsonify({'message': 'Unauthorized'}), 403

    status_filter = request.args.get('status')
    hospital_filter = request.args.get('hospital')
    search_term = request.args.get('search')

    try:
        users_query = db.collection("users")

        if status_filter:
            users_query = users_query.filter(firestore.FieldFilter("status", "==", status_filter)) # Corrected filter usage
        
        if hospital_filter:
            users_query = users_query.filter(firestore.FieldFilter("hospital", "==", hospital_filter)) # Corrected filter usage

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


# --- ADMIN: UPDATE USER STATUS, ROLE, OR HOSPITAL ---
@app.route('/admin/update-user-status', methods=['POST'])
async def update_user_status():
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_admin, admin_uid_from_token = await verify_admin_token(token) # Get admin_uid from token here
    if not is_admin:
        return jsonify({'message': 'Unauthorized'}), 403
    try:
        content = request.get_json(force=True)
        uid = content.get("uid")
        
        # Use admin_uid from token verification for audit logging.
        # The frontend will also send it, which can be a fallback/double check.
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
            updates["centerId"] = new_hospital # Ensure centerId is updated with hospital

        if not updates:
            return jsonify({'message': 'No valid fields provided for update'}), 400

        ref = db.collection("users").document(uid)
        
        # Get old user data before update for logging
        old_user_doc = ref.get()
        old_user_data = old_user_doc.to_dict() if old_user_doc.exists else {}

        ref.update(updates)

        # Log the audit event
        audit_entry = {
            "timestamp": firestore.SERVER_TIMESTAMP, # Use server timestamp
            "adminUid": requesting_admin_uid,
            "action": "user_update",
            "targetUserUid": uid,
            "changes": {},
            "oldData": {},
            "newData": {}
        }

        # Populate changes, oldData, newData for audit log
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
        
        # Add basic info about the target user
        audit_entry["targetUserEmail"] = old_user_data.get("email", "N/A")
        audit_entry["targetUserName"] = old_user_data.get("name", "N/A")


        db.collection("audit_logs").add(audit_entry)
        app.logger.info(f"Audit: User {uid} updated by {requesting_admin_uid}")

        # Re-fetch user data to send email based on latest status
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
        return jsonify({'message': str(e)}), 500

# --- ADMIN: DELETE USER ---
@app.route('/admin/delete-user', methods=['DELETE'])
async def delete_user():
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_admin, admin_uid_from_token = await verify_admin_token(token) # Get admin_uid here
    if not is_admin:
        return jsonify({'message': 'Unauthorized'}), 403

    try:
        content = request.get_json(force=True)
        uid_to_delete = content.get("uid")

        requesting_admin_uid = content.get("admin_uid", admin_uid_from_token)

        if not uid_to_delete:
            return jsonify({'message': 'Missing UID for deletion'}), 400

        # Get user data before deletion for logging
        user_doc_ref = db.collection("users").document(uid_to_delete)
        user_doc = user_doc_ref.get()
        user_data_to_log = user_doc.to_dict() if user_doc.exists else {}

        # 1. Delete user from Firebase Authentication
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

        # 2. Delete user's document from Firestore
        if user_doc.exists:
            user_doc_ref.delete()
            app.logger.info(f"Firestore user document {uid_to_delete} ({user_data_to_log.get('email')}) deleted.")
        else:
            app.logger.warning(f"Firestore user document {uid_to_delete} not found (already deleted?).")
            if sentry_sdk_configured:
                sentry_sdk.capture_message(f"Firestore user document {uid_to_delete} not found during deletion attempt.", level="warning")
        
        # Log the audit event for deletion
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

# --- ADMIN: GET HOSPITAL QA DATA ---
@app.route('/admin/hospital-data', methods=['GET'])
async def get_hospital_qa_data():
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_admin, _ = await verify_admin_token(token)
    if not is_admin:
        return jsonify({'message': 'Unauthorized'}), 403

    hospital_id = request.args.get('hospitalId')
    month_param = request.args.get('month')

    if not hospital_id or not month_param:
        return jsonify({'message': 'Missing hospitalId or month parameter'}), 400

    try:
        year, mon = map(int, month_param.split("-"))
        _, num_days = monthrange(year, mon)

        results_data = {energy: [''] * num_days for energy in ENERGY_TYPES}

        doc_ref = db.collection("linac_data").document(hospital_id).collection("months").document(f"Month_{month_param}")
        doc_snap = doc_ref.get()

        if doc_snap.exists:
            firestore_data = doc_snap.to_dict().get("data", [])
            for row in firestore_data:
                # Corrected here: removed duplicate/problematic assignment
                energy = row.get("energy")
                values = row.get("values", [])
                if energy in results_data:
                    results_data[energy] = (values + [''] * num_days)[:num_days]
        
        final_table_data = []
        for energy_type in ENERGY_TYPES:
            final_table_data.append([energy_type] + results_data[energy_type])

        return jsonify({'status': 'success', 'data': final_table_data}), 200

    except ValueError:
        app.logger.error(f"Invalid month format in /admin/hospital-data: {month_param}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_message(f"Invalid month format in /admin/hospital-data: {month_param}", level="warning")
        return jsonify({'message': 'Invalid month format. Please useYYYY-MM.'}), 400
    except Exception as e:
        app.logger.error(f"Error fetching hospital QA data for admin: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e)
        return jsonify({'message': f"Failed to fetch data: {str(e)}"}), 500

# --- Excel Export Endpoint ---
@app.route('/export-excel', methods=['POST'])
async def export_excel():
    try:
        content = request.get_json(force=True)
        uid = content.get("uid")
        month_param = content.get("month")

        if not uid or not month_param:
            return jsonify({'error': 'Missing UID or month parameter'}), 400

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
            df.to_excel(writer, index=False, sheet_name='LINAC QA Data')
        output.seek(0)

        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            download_name=f'LINAC_QA_Data_{month_param}.xlsx',
            as_attachment=True
        )

    except Exception as e:
        app.logger.error(f"Error exporting Excel file: {str(e)}", exc_info=True)
        if sentry_sdk_configured:
            sentry_sdk.capture_exception(e)
        return jsonify({'error': f"Failed to export Excel file: {str(e)}"}), 500

# --- TEMPORARY DEBUGGING ROUTE FOR SENTRY - REMOVE AFTER TESTING ---
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
