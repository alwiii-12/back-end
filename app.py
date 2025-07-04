# --- [SENTRY INTEGRATION - NEW IMPORTS AND INITIALIZATION] ---
import sentry_sdk
from sentry_sdk.integrations.flask import FlaskIntegration
import os # Make sure os is imported, which it already is.

# Retrieve Sentry DSN from environment variable
# IMPORTANT: Replace 'YOUR_SENTRY_DSN_HERE' with your actual DSN for local testing,
# but for production, make sure to set this as an environment variable in Render!
SENTRY_DSN = os.environ.get("SENTRY_DSN")

if SENTRY_DSN:
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[
            FlaskIntegration(),
        ],
        # Set traces_sample_rate to 1.0 to capture 100%
        # of transactions for performance monitoring.
        # We recommend adjusting this value in production.
        traces_sample_rate=1.0,
        # Set profiles_sample_rate to 1.0 to capture 100%
        # of active samples.
        # We recommend adjusting this value in production.
        profiles_sample_rate=1.0,
        # Enable sending of PII (Personally Identifiable Information) like user data.
        # Be careful with this in production for privacy reasons.
        send_default_pii=True
    )
    print("Sentry initialized successfully.")
else:
    print("SENTRY_DSN environment variable not set. Sentry not initialized.")


# --- [UNCHANGED IMPORTS] ---
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
# import os # Already imported above for Sentry_DSN
import json
import logging
from calendar import monthrange
from datetime import datetime, timedelta # timedelta needed for anomaly detection data fetching

import firebase_admin
from firebase_admin import credentials, firestore, auth

# New imports for Excel export
import pandas as pd
from io import BytesIO

# Imports for ML models (if you've already added them or plan to)
import joblib # For saving/loading models
import numpy as np # For numerical operations
try:
    from prophet import Prophet # Optional: for drift prediction
except ImportError:
    Prophet = None
    print("Prophet library not found. Drift prediction features will be unavailable.")


app = Flask(__name__)

# Explicitly configure CORS to allow your frontend origin
# IMPORTANT: Replace 'https://front-endnew.onrender.com' with your actual deployed frontend URL.
# For development, you might use "http://localhost:XXXX" or origins="*".
# For production, specify your exact frontend domain(s).
CORS(app, resources={r"/*": {"origins": "https://front-endnew.onrender.com"}})

app.logger.setLevel(logging.DEBUG)

# --- [EMAIL CONFIG] ---
SENDER_EMAIL = os.environ.get('SENDER_EMAIL', 'itsmealwin12@gmail.com')
RECEIVER_EMAIL = os.environ.get('RECEIVER_EMAIL', 'alwinjose812@gmail.com')
APP_PASSWORD = os.environ.get('EMAIL_APP_PASSWORD')

# --- [EMAIL SENDER FUNCTION] ---
def send_notification_email(recipient_email, subject, body):
    if not APP_PASSWORD:
        app.logger.warning(f"🚫 Cannot send notification to {recipient_email}: APP_PASSWORD not configured.")
        # If Sentry is initialized, you could capture this warning as a message
        if SENTRY_DSN:
            sentry_sdk.capture_message(f"EMAIL_APP_PASSWORD not set. Cannot send notification to {recipient_email}.")
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
        app.logger.error(f"❌ Email error: {str(e)}", exc_info=True)
        # Capture the exception with Sentry
        if SENTRY_DSN:
            sentry_sdk.capture_exception(e)
        return False

# --- [FIREBASE INIT] ---
firebase_json = os.environ.get("FIREBASE_CREDENTIALS")
if not firebase_json:
    # If Sentry is initialized, capture a critical message
    if SENTRY_DSN:
        sentry_sdk.capture_message("CRITICAL: FIREBASE_CREDENTIALS environment variable not set.", level="fatal")
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
        if SENTRY_DSN:
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
        app.logger.error("Signup failed: %s", str(e), exc_info=True)
        if SENTRY_DSN:
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
        app.logger.error("Login failed: %s", str(e), exc_info=True)
        if SENTRY_DSN:
            sentry_sdk.capture_exception(e) # Capture login errors
        return jsonify({'status': 'error', 'message': 'Login failed'}), 500

# --- SAVE DATA ---
@app.route('/save', methods=['POST'])
def save_data():
    try:
        content = request.get_json(force=True)
        uid = content.get("uid")
        month_param = content.get("month") # Use this for month_key
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
        
        # --- Anomaly Detection on newly changed or added data points ---
        anomalies_detected = []
        today_date_str = datetime.now().strftime('%Y-%m-%d') # The date for which data is being saved
        
        # This part of the code for ML is commented out for now as it needs proper
        # ML model setup (training, loading) and feature engineering.
        # It's here as a placeholder from previous discussion.
        '''
        for new_row in converted:
            energy = new_row.get("energy")
            new_values = new_row.get("values", [])
            existing_values = existing_data_map.get(energy, [])
            
            day_index = datetime.now().day - 1 # Assuming daily QA is for today
            
            if energy in ENERGY_TYPES and day_index < len(new_values):
                try:
                    current_value = float(new_values[day_index])
                    if day_index >= len(existing_values) or float(existing_values[day_index] or 0) != current_value:
                        
                        anomaly_model = load_model(center_id, energy, 'anomaly')
                        if anomaly_model:
                            # Placeholder for actual feature generation for today's data point
                            test_features = np.array([[
                                current_value, 
                                datetime.now().weekday(), 
                                datetime.now().day
                            ]])
                            
                            prediction = anomaly_model.predict(test_features)
                            if prediction[0] == -1: # -1 indicates an outlier/anomaly
                                anomalies_detected.append({
                                    'energy': energy,
                                    'date': today_date_str,
                                    'value': current_value,
                                    'type': 'anomaly'
                                })
                                app.logger.warning(f"Anomaly detected for {energy} at {center_id} on {today_date_str}: {current_value}%")
                        else:
                            app.logger.warning(f"Anomaly model not found for {energy} at {center_id}. Skipping detection.")

                except (ValueError, TypeError) as val_e:
                    app.logger.warning(f"Non-numeric value encountered for anomaly detection: {val_e}")
                except Exception as ad_e:
                    app.logger.error(f"Error during anomaly detection for {energy}: {ad_e}", exc_info=True)
        '''
        
        if anomalies_detected:
            app.logger.info(f"Anomalies detected during save: {anomalies_detected}")

        return jsonify({'status': 'success', 'anomalies': anomalies_detected}), 200
    except Exception as e:
        app.logger.error("Save data failed: %s", str(e), exc_info=True)
        if SENTRY_DSN:
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
        app.logger.error("Get data failed: %s", str(e), exc_info=True)
        if SENTRY_DSN:
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
            if SENTRY_DSN:
                sentry_sdk.capture_message("Missing UID or month key for alert processing.", level="warning")
            return jsonify({'status': 'error', 'message': 'Missing UID or month for alert processing'}), 400

        user_doc = db.collection('users').document(uid).get()
        if not user_doc.exists:
            app.logger.warning(f"User document not found for UID: {uid} during alert processing.")
            if SENTRY_DSN:
                sentry_sdk.capture_message(f"User document not found for UID: {uid} during alert processing.", level="warning")
            return jsonify({'status': 'error', 'message': 'User not found for alert processing'}), 404
        user_data = user_doc.to_dict()
        center_id = user_data.get('centerId')

        if not center_id:
            app.logger.warning(f"Center ID not found for user {uid} during alert processing.")
            if SENTRY_DSN:
                sentry_sdk.capture_message(f"Center ID not found for user {uid} during alert processing.", level="warning")
            return jsonify({'status': 'error', 'message': 'Center ID not found for user for alert processing'}), 400

        rso_emails = []
        try: # Nested try for rso_emails fetching
            rso_users = db.collection('users').where('centerId', '==', center_id).where('role', '==', 'RSO').stream()
            for rso_user in rso_users:
                rso_data = rso_user.to_dict()
                if 'email' in rso_data and rso_data['email']:
                    rso_emails.append(rso_data['email'])
            
            if not rso_emails:
                app.logger.warning(f"No RSO email found for centerId: {center_id}. Alert not sent to RSO.")
                if SENTRY_DSN:
                    sentry_sdk.capture_message(f"No RSO email found for centerId: {center_id}. Alert not sent.", level="info")
                return jsonify({'status': 'no_rso_email', 'message': 'No RSO email found for this hospital.'}), 200

        except Exception as e:
            app.logger.error(f"Error fetching RSO emails for center {center_id}: {str(e)}", exc_info=True)
            if SENTRY_DSN:
                sentry_sdk.capture_exception(e)
            return jsonify({'status': 'error', 'message': 'Failed to fetch RSO emails'}), 500

        if not APP_PASSWORD:
            app.logger.warning("APP_PASSWORD not configured. Cannot send email.")
            if SENTRY_DSN:
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
        
        # Using the send_notification_email helper function here
        email_sent = send_notification_email(", ".join(rso_emails), f"⚠ LINAC QA Status - {hospital} ({month_key})", message_body)

        if email_sent:
            month_alerts_doc_ref.set({"alerted_values": current_out_values}, merge=False)
            app.logger.debug(f"Alert state updated in Firestore for {center_id}/{month_key}.")
            return jsonify({'status': 'alert sent', 'message': 'Email sent and alert state updated.'}), 200
        else:
            return jsonify({'status': 'email_send_error', 'message': 'Failed to send email via helper function.'}), 500

    except Exception as e:
        app.logger.error(f"Error in send_alert function: {str(e)}", exc_info=True)
        if SENTRY_DSN:
            sentry_sdk.capture_exception(e) # Capture general send_alert errors
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
        date_param = content.get("date") # Expected format:YYYY-MM-DD

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
                                if i < len(date_strings): # Ensure index is valid
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
                return jsonify({'status': 'error', 'message': 'Invalid date format. Please useYYYY-MM-DD.'}), 400


            found_value = None
            found_status = "N/A"

            for row in data_rows:
                if row.get("energy") == energy_type:
                    values = row.get("values", [])
                    if day_index < len(values): # Ensure day_index is within the bounds of collected values
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
                        if abs(n) > 1.8 and abs(n) <= 2.0: # 'Warning' range
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
        if SENTRY_DSN:
            sentry_sdk.capture_exception(e) # Capture chatbot errors
        return jsonify({'status': 'error', 'message': str(e)}), 500

# --- ADMIN: GET PENDING USERS ---
@app.route('/admin/pending-users', methods=['GET'])
async def get_pending_users():
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_admin, _ = await verify_admin_token(token)
    if not is_admin:
        return jsonify({'message': 'Unauthorized'}), 403
    try:
        users = db.collection("users").where("status", "==", "pending").stream()
        return jsonify([doc.to_dict() | {"uid": doc.id} for doc in users]), 200
    except Exception as e:
        app.logger.error("Get pending users failed: %s", str(e), exc_info=True)
        if SENTRY_DSN:
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
    search_term = request.args.get('search') # For general search (email, name, role, hospital)

    try:
        users_query = db.collection("users")

        if status_filter:
            users_query = users_query.where("status", "==", status_filter)
        
        if hospital_filter:
            users_query = users_query.where("hospital", "==", hospital_filter)
            # Firestore limitations: Cannot combine '==' queries on different fields without a composite index.
            # For general search_term, we'll fetch all matching current filters and then filter in Python.

        users_stream = users_query.stream()
        
        all_users = []
        for doc in users_stream:
            user_data = doc.to_dict()
            user_data['uid'] = doc.id # Add UID to the dictionary

            # Apply general search_term filtering in Python
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
        if SENTRY_DSN:
            sentry_sdk.capture_exception(e)
        return jsonify({'message': str(e)}), 500


# --- ADMIN: UPDATE USER STATUS, ROLE, OR HOSPITAL ---
@app.route('/admin/update-user-status', methods=['POST'])
async def update_user_status():
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_admin, admin_uid = await verify_admin_token(token)
    if not is_admin:
        return jsonify({'message': 'Unauthorized'}), 403
    try:
        content = request.get_json(force=True)
        uid = content.get("uid")
        
        # New fields that can be updated
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
        ref.update(updates)

        # Re-fetch user data to send email based on latest status
        updated_user_data = ref.get().to_dict()
        # Using the send_notification_email helper function here
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
            if SENTRY_DSN:
                sentry_sdk.capture_message(f"No email for user {uid} found to send update notification.", level="warning")

        return jsonify({'status': 'success', 'message': 'User updated successfully'}), 200
    except Exception as e:
        app.logger.error(f"Error updating user status/role/hospital: {str(e)}", exc_info=True)
        if SENTRY_DSN:
            sentry_sdk.capture_exception(e)
        return jsonify({'message': str(e)}), 500

# --- ADMIN: DELETE USER ---
@app.route('/admin/delete-user', methods=['DELETE'])
async def delete_user():
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_admin, _ = await verify_admin_token(token)
    if not is_admin:
        return jsonify({'message': 'Unauthorized'}), 403

    try:
        content = request.get_json(force=True)
        uid_to_delete = content.get("uid")

        if not uid_to_delete:
            return jsonify({'message': 'Missing UID for deletion'}), 400

        # 1. Delete user from Firebase Authentication
        try:
            auth.delete_user(uid_to_delete)
            app.logger.info(f"Firebase Auth user {uid_to_delete} deleted.")
        except Exception as e:
            if "User record not found" in str(e):
                app.logger.warning(f"Firebase Auth user {uid_to_delete} not found, proceeding with Firestore deletion.")
                if SENTRY_DSN:
                    sentry_sdk.capture_message(f"Firebase Auth user {uid_to_delete} not found during deletion attempt.", level="warning")
            else:
                app.logger.error(f"Error deleting Firebase Auth user {uid_to_delete}: {str(e)}", exc_info=True)
                if SENTRY_DSN:
                    sentry_sdk.capture_exception(e)
                return jsonify({'message': f"Failed to delete Firebase Auth user: {str(e)}"}), 500

        # 2. Delete user's document from Firestore
        user_doc_ref = db.collection("users").document(uid_to_delete)
        user_doc = user_doc_ref.get() # Get doc to log data before deleting

        if user_doc.exists:
            user_data = user_doc.to_dict()
            user_doc_ref.delete()
            app.logger.info(f"Firestore user document {uid_to_delete} ({user_data.get('email')}) deleted.")
        else:
            app.logger.warning(f"Firestore user document {uid_to_delete} not found (already deleted?).")
            if SENTRY_DSN:
                sentry_sdk.capture_message(f"Firestore user document {uid_to_delete} not found during deletion attempt.", level="warning")
        
        return jsonify({'status': 'success', 'message': 'User deleted successfully'}), 200

    except Exception as e:
        app.logger.error(f"Error deleting user: {str(e)}", exc_info=True)
        if SENTRY_DSN:
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
    month_param = request.args.get('month') # Expected format:YYYY-MM

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
                energy = row.get("energy")
                values = row.get("values", [])
                if energy in results_data:
                    results_data[energy] = (values + [''] * num_days)[:num_days]
        
        final_table_data = []
        for energy_type in ENERGY_TYPES:
            final_table_data.append([energy_type] + results_data[energy_type])

        return jsonify({'status': 'success', 'data': final_table_data}), 200

    except ValueError:
        return jsonify({'message': 'Invalid month format. Please useYYYY-MM-DD.'}), 400
    except Exception as e:
        app.logger.error(f"Error fetching hospital QA data for admin: {str(e)}", exc_info=True)
        if SENTRY_DSN:
            sentry_sdk.capture_exception(e)
        return jsonify({'message': f"Failed to fetch data: {str(e)}"}), 500

# --- Excel Export Endpoint ---
@app.route('/export-excel', methods=['POST'])
async def export_excel():
    try:
        content = request.get_json(force=True)
        uid = content.get("uid")
        month_param = content.get("month") # YYYY-MM

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
        if SENTRY_DSN:
            sentry_sdk.capture_exception(e)
        return jsonify({'error': f"Failed to export Excel file: {str(e)}"}), 500

# --- ML Model Management Endpoints (PLACEHOLDERS) ---
# These routes are commented out as they depend on ML setup (joblib, prophet etc.)
# and need to be fully implemented with data fetching and model persistence logic.

# # Helper to load/save models - can be extended to use cloud storage or a dedicated folder
# MODEL_DIR = 'ml_models'
# os.makedirs(MODEL_DIR, exist_ok=True) # Ensure this directory exists and is writable on Render

# def save_model(model, center_id, energy_type, model_type):
#     """Saves a trained model."""
#     filepath = os.path.join(MODEL_DIR, f"{center_id}_{energy_type}_{model_type}.pkl")
#     try:
#         import joblib
#         joblib.dump(model, filepath)
#         app.logger.info(f"Model saved: {filepath}")
#     except Exception as e:
#         app.logger.error(f"Failed to save model {filepath}: {e}")
#         if SENTRY_DSN: sentry_sdk.capture_exception(e)

# def load_model(center_id, energy_type, model_type):
#     """Loads a trained model."""
#     filepath = os.path.join(MODEL_DIR, f"{center_id}_{energy_type}_{model_type}.pkl")
#     try:
#         import joblib
#         if os.path.exists(filepath):
#             model = joblib.load(filepath)
#             app.logger.info(f"Model loaded: {filepath}")
#             return model
#         else:
#             app.logger.warning(f"Model not found: {filepath}")
#             if SENTRY_DSN: sentry_sdk.capture_message(f"ML Model not found: {filepath}", level="warning")
#             return None
#     except Exception as e:
#         app.logger.error(f"Failed to load model {filepath}: {e}")
#         if SENTRY_DSN: sentry_sdk.capture_exception(e)
#         return None

# def get_ml_data_for_energy(center_id, energy_type, days_back=180):
#     """
#     Fetches historical QA data for a specific energy type and prepares it for ML.
#     Returns a pandas DataFrame.
#     """
#     # ... (Implementation from previous response) ...
#     pass # Placeholder

# @app.route('/admin/train-anomaly-model', methods=['POST'])
# async def train_anomaly_model():
#     # ... (Implementation from previous response) ...
#     pass # Placeholder

# @app.route('/admin/train-drift-model', methods=['POST'])
# async def train_drift_model():
#     # ... (Implementation from previous response) ...
#     pass # Placeholder

# @app.route('/predict-drift', methods=['GET'])
# async def predict_drift():
#     # ... (Implementation from previous response) ...
#     pass # Placeholder


# --- INDEX ---
@app.route('/')
def index():
    return "✅ LINAC QA Backend Running"

# --- RUN ---
if __name__ == '__main__':
    app.run(debug=True)
