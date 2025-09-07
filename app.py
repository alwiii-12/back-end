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



# New imports for Excel export and historical forecast

import pandas as pd

from io import BytesIO

from prophet import Prophet





import numpy as np 



# Set nlp to None explicitly, as it's no longer loaded

nlp = None # This makes sure the 'nlp is None' check always passes





app = Flask(__name__)



# --- [CORS CONFIGURATION] ---

origins = [

    "https://front-endnew.onrender.com",

    "http://127.0.0.1:5500", # For local testing

    "http://localhost:5500"  # For local testing

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

    if request.method == 'OPTIONS' or request.path == '/':

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



# --- ANNOTATION ENDPOINTS ---

@app.route('/save-annotation', methods=['POST'])

def save_annotation():

    try:

        content = request.get_json(force=True)

        uid = content.get("uid")

        month = content.get("month")

        key = content.get("key")

        data = content.get("data")



        if not all([uid, month, key, data]):

            return jsonify({'status': 'error', 'message': 'Missing required fields'}), 400



        annotation_ref = db.collection('annotations').document(uid).collection(month).document(key)

        annotation_ref.set(data)



        is_service_event = data.get('isServiceEvent', False)

        event_date = data.get('eventDate')

        

        if event_date:

            service_event_ref = db.collection('service_events').document(uid).collection('events').document(event_date)

            if is_service_event:

                service_event_ref.set({

                    'description': data.get('text', 'Service/Calibration'),

                    'energy': data.get('energy'),

                    'dataType': data.get('dataType')

                })

                app.logger.info(f"Service event marked for user {uid} on date {event_date}")

            else:

                service_event_ref.delete()

                app.logger.info(f"Service event unmarked for user {uid} on date {event_date}")



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

        uid = content.get("uid")

        month = content.get("month")

        key = content.get("key")



        if not all([uid, month, key]):

            return jsonify({'status': 'error', 'message': 'Missing required fields'}), 400

        

        annotation_ref = db.collection('annotations').document(uid).collection(month).document(key)

        annotation_ref.delete()



        event_date = key.split('-', 1)[1]

        if event_date:

            service_event_ref = db.collection('service_events').document(uid).collection('events').document(event_date)

            service_event_ref.delete()

            app.logger.info(f"Deleted service event for user {uid} on date {event_date} along with annotation.")



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

            "hospital": user_data.get("hospital", "N/A").lower().replace(" ", "_"),

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



# --- DATA & ALERT ENDPOINTS ---

def create_proactive_chat(uid, data_type, energy, value):

    topic = None

    if data_type == "output":

        topic = "output_drift"

    elif data_type in ["flatness", "inline", "crossline"]:

        topic = "symmetry_horn_fault" 

    

    if topic:

        chat_ref = db.collection('proactive_chats').document()

        chat_ref.set({

            'uid': uid, 'timestamp': firestore.SERVER_TIMESTAMP, 'read': False, 'topic': topic,

            'initial_message': f"I noticed a QA value in the warning range for {data_type.title()} on {energy} (value: {value}%). Would you like me to help diagnose the issue?"

        })

        app.logger.info(f"Created proactive chat for user {uid} regarding {data_type} warning.")



@app.route('/save', methods=['POST'])

def save_data():

    try:

        content = request.get_json(force=True)

        uid, month_param, raw_data, data_type = content.get("uid"), content.get("month"), content.get("data"), content.get("dataType")



        if not data_type or data_type not in DATA_TYPES:

            return jsonify({'status': 'error', 'message': 'Invalid or missing dataType'}), 400



        user_doc = db.collection('users').document(uid).get()

        if not user_doc.exists: return jsonify({'status': 'error', 'message': 'User not found'}), 404

        

        user_data = user_doc.to_dict()

        center_id, user_status = user_data.get("centerId"), user_data.get("status", "pending")



        if user_status != "active": return jsonify({'status': 'error', 'message': 'Account not active'}), 403

        if not center_id: return jsonify({'status': 'error', 'message': 'Missing centerId'}), 400

        if not isinstance(raw_data, list): return jsonify({'status': 'error', 'message': 'Invalid data'}), 400



        month_doc_id = f"Month_{month_param}"

        converted = [{"row": i, "energy": row[0], "values": row[1:]} for i, row in enumerate(raw_data) if len(row) > 1]

        

        doc_ref = db.collection("linac_data").document(center_id).collection("months").document(month_doc_id)

        doc_ref.set({f"data_{data_type}": converted}, merge=True)

        

        config = DATA_TYPE_CONFIGS.get(data_type)

        if config:

            warning_found = False

            for item in converted:

                if warning_found: break

                energy = item.get("energy")

                for value_str in item.get("values", []):

                    try:

                        value = float(value_str)

                        if config["warning"] < abs(value) <= config["tolerance"]:

                            create_proactive_chat(uid, data_type, energy, value)

                            warning_found = True

                            break

                    except (ValueError, TypeError): continue



        return jsonify({'status': 'success', 'message': f'{data_type} data saved successfully'}), 200

    except Exception as e:

        app.logger.error(f"Save data failed for {data_type}: {str(e)}", exc_info=True)

        if sentry_sdk_configured:

            sentry_sdk.capture_exception(e)

        return jsonify({'status': 'error', 'message': str(e)}), 500



@app.route('/data', methods=['GET'])

def get_data():

    try:

        month_param, uid, data_type = request.args.get('month'), request.args.get('uid'), request.args.get('dataType')

        if not all([month_param, uid, data_type]) or data_type not in DATA_TYPES:

            return jsonify({'error': 'Invalid or missing parameters'}), 400



        user_doc = db.collection("users").document(uid).get()

        if not user_doc.exists: return jsonify({'error': 'User not found'}), 404

        

        user_data = user_doc.to_dict()

        center_id, user_status = user_data.get("centerId"), user_data.get("status", "pending")



        if user_status != "active": return jsonify({'error': 'Account not active'}), 403

        if not center_id: return jsonify({'error': 'Missing centerId'}), 400



        year, mon = map(int, month_param.split("-"))

        _, num_days = monthrange(year, mon)

        energy_dict = {e: [""] * num_days for e in ENERGY_TYPES}

        

        doc = db.collection("linac_data").document(center_id).collection("months").document(f"Month_{month_param}").get()

        if doc.exists:

            for row in doc.to_dict().get(f"data_{data_type}", []):

                energy, values = row.get("energy"), row.get("values", [])

                if energy in energy_dict:

                    energy_dict[energy] = (values + [""] * num_days)[:num_days]



        return jsonify({'data': [[e] + energy_dict[e] for e in ENERGY_TYPES]}), 200

    except Exception as e:

        app.logger.error(f"Get data failed for {data_type}: {str(e)}", exc_info=True)

        if sentry_sdk_configured:

            sentry_sdk.capture_exception(e)

        return jsonify({'error': str(e)}), 500



@app.route('/send-alert', methods=['POST'])

def send_alert():

    try:

        content = request.get_json(force=True)

        uid = content.get("uid")

        user_doc = db.collection('users').document(uid).get()

        if not user_doc.exists: return jsonify({'status': 'error', 'message': 'User not found'}), 404

        

        user_data = user_doc.to_dict()

        center_id = user_data.get('centerId')

        if not center_id: return jsonify({'status': 'error', 'message': 'Center ID not found for user'}), 400



        rso_users_query = db.collection('users').where('centerId', '==', center_id).where('role', '==', 'RSO')

        rso_emails = [rso.to_dict()['email'] for rso in rso_users_query.stream() if 'email' in rso.to_dict()]

        

        if not rso_emails: return jsonify({'status': 'no_rso_email', 'message': 'No RSO email found.'}), 200

        

        current_out_values, hospital, month_key, data_type, tolerance = content.get("outValues", []), content.get("hospitalName"), content.get("month"), content.get("dataType", "output"), content.get("tolerance", 2.0)

        data_type_display = data_type.replace("_", " ").title()



        alerts_doc_ref = db.collection("linac_alerts").document(center_id).collection("months").document(f"Month_{month_key}_{data_type}")

        alerts_doc = alerts_doc_ref.get()

        

        previously_alerted_strings = set(json.dumps(val, sort_keys=True) for val in (alerts_doc.to_dict().get("alerted_values", []) if alerts_doc.exists else []))

        current_out_values_strings = set(json.dumps(val, sort_keys=True) for val in current_out_values)



        if current_out_values_strings == previously_alerted_strings:

            return jsonify({'status': 'no_change', 'message': 'No new alerts.'})



        message_body = f"{data_type_display} QA Status for {hospital} ({month_key})\n\n"

        if current_out_values:

            message_body += f"Out-of-Tolerance Values (±{tolerance}%):\n\n"

            for v in sorted(current_out_values, key=lambda x: (x.get('energy'), x.get('date'))):

                message_body += f"Energy: {v.get('energy', 'N/A')}, Date: {v.get('date', 'N/A')}, Value: {v.get('value', 'N/A')}%\n"

        else:

            message_body += f"All previously detected {data_type_display} issues are resolved.\n"



        if send_notification_email(", ".join(rso_emails), f"⚠ {data_type_display} QA Status - {hospital} ({month_key})", message_body):

            alerts_doc_ref.set({"alerted_values": current_out_values})

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

        uid, data_type, energy, month = request.args.get('uid'), request.args.get('dataType'), request.args.get('energy'), request.args.get('month')

        if not all([uid, data_type, energy, month]): return jsonify({'error': 'Missing parameters'}), 400



        user_doc = db.collection("users").document(uid).get()

        if not user_doc.exists: return jsonify({'error': 'User not found'}), 404

        

        center_id = user_doc.to_dict().get("centerId")

        if not center_id: return jsonify({'error': 'User has no center'}), 400



        prediction_doc = db.collection("linac_predictions").document(f"{center_id}_{data_type}_{energy}_{month}").get()

        return jsonify(prediction_doc.to_dict()) if prediction_doc.exists else (jsonify({'error': f'Prediction not found'}), 404)

    except Exception as e:

        app.logger.error(f"Get predictions failed: {str(e)}", exc_info=True)

        if sentry_sdk_configured:

            sentry_sdk.capture_exception(e)

        return jsonify({'error': str(e)}), 500



@app.route('/historical-forecast', methods=['POST'])

def get_historical_forecast():

    try:

        content = request.get_json(force=True)

        uid, month_param, data_type, energy = content.get('uid'), content.get('month'), content.get('dataType'), content.get('energy')

        if not all([uid, month_param, data_type, energy]): return jsonify({'error': 'Missing parameters'}), 400



        user_doc = db.collection("users").document(uid).get()

        if not user_doc.exists: return jsonify({'error': 'User not found'}), 404

        center_id = user_doc.to_dict().get("centerId")



        all_values = []

        end_date_for_training = pd.to_datetime(month_param) - timedelta(days=1)



        for month_doc in db.collection("linac_data").document(center_id).collection("months").stream():

            month_id_str = month_doc.id.replace("Month_", "")

            if pd.to_datetime(month_id_str) > end_date_for_training: continue

            

            for row_data in month_doc.to_dict().get(f"data_{data_type}", []):

                if row_data.get("energy") == energy:

                    year, mon = map(int, month_id_str.split("-"))

                    for i, value in enumerate(row_data.get("values", [])):

                        try:

                            if value and i + 1 <= monthrange(year, mon)[1]:

                                all_values.append({"ds": pd.to_datetime(f"{year}-{mon}-{i+1}"), "y": float(value)})

                        except (ValueError, TypeError): continue

        

        df = pd.DataFrame(all_values).sort_values(by="ds").drop_duplicates(subset='ds', keep='last')

        if len(df) < 10: return jsonify({'error': 'Not enough historical data.'}), 404



        model = Prophet().fit(df)

        year, mon = map(int, month_param.split("-"))

        num_days = monthrange(year, mon)[1]

        future = pd.DataFrame({'ds': pd.date_range(start=f"{year}-{mon}-01", periods=num_days, freq='D')})

        forecast_df = model.predict(future)

        

        actuals = []

        doc = db.collection("linac_data").document(center_id).collection("months").document(f"Month_{month_param}").get()

        if doc.exists:

            energy_row = next((item for item in doc.to_dict().get(f"data_{data_type}", []) if item["energy"] == energy), None)

            if energy_row:

                values = energy_row.get("values", [])

                actuals = [(float(v) if v not in [None, ''] else None) for v in values]

                actuals = (actuals + [None] * num_days)[:num_days]



        return jsonify({

            'forecast': forecast_df[['ds', 'yhat', 'yhat_lower', 'yhat_upper']].to_dict('records'),

            'actuals': actuals

        }), 200

    except Exception as e:

        app.logger.error(f"Historical forecast failed: {str(e)}", exc_info=True)

        return jsonify({'error': str(e)}), 500



# --- DASHBOARD & CHATBOT FUNCTIONS ---

@app.route('/dashboard-summary', methods=['GET'])

def get_dashboard_summary():

    token = request.headers.get("Authorization", "").split("Bearer ")[-1]

    is_admin, _ = verify_admin_token(token)

    if not is_admin: return jsonify({'message': 'Unauthorized'}), 403

    

    try:

        month_key = request.args.get('month', datetime.now().strftime('%Y-%m'))

        unique_hospitals = {user.to_dict().get('hospital') for user in db.collection('users').stream() if user.to_dict().get('hospital')}

        leaderboard = [ {"hospital": h, **get_monthly_summary(h, month_key)} for h in sorted(list(unique_hospitals)) ]

        pending_users_count = len(list(db.collection("users").where('status', '==', "pending").stream()))

        

        leaderboard.sort(key=lambda x: (x['oot'], x['warnings']), reverse=True)

        return jsonify({

            "pending_users_count": pending_users_count,

            "total_warnings": sum(h['warnings'] for h in leaderboard),

            "total_oot": sum(h['oot'] for h in leaderboard),

            "leaderboard": leaderboard

        }), 200

    except Exception as e:

        app.logger.error(f"Error getting dashboard summary: {str(e)}", exc_info=True)

        if sentry_sdk_configured:

            sentry_sdk.capture_exception(e)

        return jsonify({'message': str(e)}), 500



def get_monthly_summary(center_id, month_key):

    warnings, oot = 0, 0

    doc = db.collection("linac_data").document(center_id).collection("months").document(f"Month_{month_key}").get()

    if doc.exists:

        for data_type, config in DATA_TYPE_CONFIGS.items():

            for row in doc.to_dict().get(f"data_{data_type}", []):

                for value in row.get("values", []):

                    try:

                        val = abs(float(value))

                        if val > config["tolerance"]: oot += 1

                        elif val > config["warning"]: warnings += 1

                    except (ValueError, TypeError): continue

    return {"warnings": warnings, "oot": oot}



@app.route('/query-qa-data', methods=['POST'])

def query_qa_data():

    try:

        content = request.get_json(force=True)

        user_query_text = content.get("query_text", "").lower()



        with open('knowledge_base.json', 'r') as f:

            kb = json.load(f)



        topic = 'output_drift' if 'drift' in user_query_text or 'output' in user_query_text else \

                'symmetry_horn_fault' if 'flatness' in user_query_text or 'symmetry' in user_query_text else None



        if not topic:

            for keyword, path in kb.get("maintenance_info", {}).items():

                if keyword.replace("_", " ") in user_query_text:

                    return jsonify({'status': 'success', 'message': path})

            return jsonify({'status': 'error', 'message': "I can help diagnose issues with 'output drift' or 'flatness/symmetry'. What would you like to diagnose?"}), 404



        flow = kb.get("troubleshooting", {}).get(topic)

        start_node = flow.get('nodes', {}).get(flow.get('start_node'))



        return jsonify({

            'status': 'diagnostic_start', 'topic': topic, 'node_id': flow.get('start_node'),

            'question': start_node.get('question'), 'options': start_node.get('options', [])

        })

    except Exception as e:

        app.logger.error(f"Chatbot query failed: {str(e)}", exc_info=True)

        if sentry_sdk_configured:

            sentry_sdk.capture_exception(e)

        return jsonify({'status': 'error', 'message': str(e)}), 500



@app.route('/diagnose-step', methods=['POST'])

def diagnose_step():

    try:

        content = request.get_json(force=True)

        topic, current_node_id, answer = content.get("topic"), content.get("node_id"), content.get("answer")

        if not all([topic, current_node_id, answer]): return jsonify({'status': 'error', 'message': 'Missing parameters'}), 400



        with open('knowledge_base.json', 'r') as f:

            kb = json.load(f)



        flow = kb.get("troubleshooting", {}).get(topic)

        current_node = flow.get("nodes", {}).get(current_node_id)

        next_node_id = current_node.get("answers", {}).get(answer)

        next_node = flow.get("nodes", {}).get(next_node_id)



        if "diagnosis" in next_node:

            return jsonify({'status': 'diagnostic_end', 'diagnosis': next_node.get('diagnosis')})

        elif "question" in next_node:

            return jsonify({

                'status': 'diagnostic_continue', 'topic': topic, 'node_id': next_node_id,

                'question': next_node.get('question'), 'options': next_node.get('options', [])

            })

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

        action, user_uid = content.get("action"), content.get("userUid")

        if not all([action, user_uid]): return jsonify({'status': 'error', 'message': 'Missing action or userUid'}), 400



        user_doc = db.collection('users').document(user_uid).get()

        user_data = user_doc.to_dict() if user_doc.exists else {}

        

        db.collection("audit_logs").add({

            "timestamp": firestore.SERVER_TIMESTAMP, "action": action, "targetUserUid": user_uid,

            "hospital": user_data.get("hospital", "N/A").lower().replace(" ", "_"),

            "details": { "user_email": user_data.get("email", "N/A"), "user_agent": request.headers.get('User-Agent') }

        })

        return jsonify({'status': 'success', 'message': 'Event logged'}), 200

    except Exception as e:

        app.logger.error(f"Error logging event: {str(e)}", exc_info=True)

        if sentry_sdk_configured:

            sentry_sdk.capture_exception(e)

        return jsonify({'status': 'error', 'message': str(e)}), 500



# --- ADMIN ENDPOINTS ---

@app.route('/admin/users', methods=['GET'])

def get_all_users():

    token = request.headers.get("Authorization", "").split("Bearer ")[-1]

    is_admin, _ = verify_admin_token(token)

    if not is_admin: return jsonify({'message': 'Unauthorized'}), 403

    try:

        return jsonify([doc.to_dict() | {"uid": doc.id} for doc in db.collection("users").stream()]), 200

    except Exception as e:

        app.logger.error(f"Get all users failed: {str(e)}", exc_info=True)

        if sentry_sdk_configured:

            sentry_sdk.capture_exception(e)

        return jsonify({'message': str(e)}), 500



@app.route('/admin/update-user-status', methods=['POST'])

def update_user_status():

    token = request.headers.get("Authorization", "").split("Bearer ")[-1]

    is_admin, admin_uid_from_token = verify_admin_token(token)

    if not is_admin: return jsonify({'message': 'Unauthorized'}), 403

    try:

        content = request.get_json(force=True)

        uid, new_status, new_role, new_hospital = content.get("uid"), content.get("status"), content.get("role"), content.get("hospital")

        requesting_admin_uid = content.get("admin_uid", admin_uid_from_token) 

        if not uid: return jsonify({'message': 'UID is required'}), 400

        

        updates = {}

        if new_status in ["active", "pending", "rejected"]: updates["status"] = new_status

        if new_role in ["Medical physicist", "RSO", "Admin"]: updates["role"] = new_role

        if new_hospital and new_hospital.strip() != "":

            updates["hospital"] = new_hospital

            updates["centerId"] = new_hospital

        if not updates: return jsonify({'message': 'No valid fields provided for update'}), 400



        ref = db.collection("users").document(uid)

        old_user_doc = ref.get()

        old_user_data = old_user_doc.to_dict() if old_user_doc.exists else {}

        ref.update(updates)



        audit_entry = {"timestamp": firestore.SERVER_TIMESTAMP, "adminUid": requesting_admin_uid, "action": "user_update", "targetUserUid": uid, "changes": {}, "hospital": old_user_data.get("hospital", "N/A").lower().replace(" ", "_")}

        if "status" in updates: audit_entry["changes"]["status"] = {"old": old_user_data.get("status"), "new": updates["status"]}

        if "role" in updates: audit_entry["changes"]["role"] = {"old": old_user_data.get("role"), "new": updates["role"]}

        if "hospital" in updates: audit_entry["changes"]["hospital"] = {"old": old_user_data.get("hospital"), "new": updates["hospital"]}

        db.collection("audit_logs").add(audit_entry)

        

        updated_user_data = ref.get().to_dict()

        if updated_user_data.get("email"):

            subject, body = "LINAC QA Account Update", "Your LINAC QA account details have been updated."

            if "status" in updates: body += f"\nYour account status is now: {updates['status'].upper()}."

            if "role" in updates: body += f"\nYour role has been updated to: {updates['role']}."

            if "hospital" in updates: body += f"\nYour hospital has been updated to: {updates['hospital']}."

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

    if not is_admin: return jsonify({'message': 'Unauthorized'}), 403

    try:

        content = request.get_json(force=True)

        uid_to_delete, requesting_admin_uid = content.get("uid"), content.get("admin_uid", admin_uid_from_token)

        if not uid_to_delete: return jsonify({'message': 'Missing UID for deletion'}), 400



        user_doc_ref = db.collection("users").document(uid_to_delete)

        user_data_to_log = user_doc_ref.get().to_dict() or {}

        try: auth.delete_user(uid_to_delete)

        except Exception as e:

            if "User record not found" not in str(e):

                app.logger.error(f"Error deleting Firebase Auth user {uid_to_delete}: {str(e)}", exc_info=True)

                return jsonify({'message': f"Failed to delete Firebase Auth user: {str(e)}"}), 500

        user_doc_ref.delete()

        

        db.collection("audit_logs").add({"timestamp": firestore.SERVER_TIMESTAMP, "adminUid": requesting_admin_uid, "action": "user_deletion", "targetUserUid": uid_to_delete, "deletedUserData": user_data_to_log})

        return jsonify({'status': 'success', 'message': 'User deleted successfully'}), 200

    except Exception as e:

        app.logger.error(f"Error deleting user: {str(e)}", exc_info=True)

        if sentry_sdk_configured:

            sentry_sdk.capture_exception(e)

        return jsonify({'message': f"Failed to delete user: {str(e)}"}), 500



@app.route('/admin/hospital-data', methods=['GET'])

def get_hospital_data():

    token = request.headers.get("Authorization", "").split("Bearer ")[-1]

    is_admin, _ = verify_admin_token(token)

    if not is_admin: return jsonify({'message': 'Unauthorized'}), 403

    try:

        hospital_id, month_param = request.args.get('hospitalId'), request.args.get('month')

        if not hospital_id or not month_param: return jsonify({'error': 'Missing hospitalId or month parameter'}), 400

        year, mon = map(int, month_param.split("-"))

        _, num_days = monthrange(year, mon)

        all_data = {}

        for data_type in DATA_TYPES:

            doc = db.collection("linac_data").document(hospital_id).collection("months").document(f"Month_{month_param}").get()

            energy_dict = {e: [""] * num_days for e in ENERGY_TYPES}

            if doc.exists:

                for row in doc.to_dict().get(f"data_{data_type}", []):

                    energy, values = row.get("energy"), row.get("values", [])

                    if energy in energy_dict: energy_dict[energy] = (values + [""] * num_days)[:num_days]

            all_data[data_type] = [[e] + energy_dict[e] for e in ENERGY_TYPES]

        return jsonify({'data': all_data}), 200

    except Exception as e:

        app.logger.error(f"Admin get hospital data failed: {str(e)}", exc_info=True)

        if sentry_sdk_configured:

            sentry_sdk.capture_exception(e)

        return jsonify({'error': str(e)}), 500



@app.route('/admin/audit-logs', methods=['GET'])

def get_audit_logs():

    token = request.headers.get("Authorization", "").split("Bearer ")[-1]

    is_admin, _ = verify_admin_token(token)

    if not is_admin: return jsonify({'message': 'Unauthorized'}), 403

    try:

        logs_query = db.collection("audit_logs").order_by("timestamp", direction=firestore.Query.DESCENDING)

        hospital_id, action, date_str = request.args.get('hospitalId'), request.args.get('action'), request.args.get('date')

        if hospital_id: logs_query = logs_query.where('hospital', '==', hospital_id)

        if action: logs_query = logs_query.where('action', '==', action)

        if date_str:

            start_dt = datetime.strptime(date_str, '%Y-%m-%d').replace(tzinfo=pytz.UTC)

            logs_query = logs_query.where('timestamp', '>=', start_dt).where('timestamp', '<', start_dt + timedelta(days=1))

        

        logs, user_cache = [], {}

        for doc in logs_query.limit(200).stream():

            log_data = doc.to_dict()

            if 'timestamp' in log_data and isinstance(log_data['timestamp'], datetime): log_data['timestamp'] = log_data['timestamp'].astimezone(pytz.timezone('Asia/Kolkata')).strftime('%Y-%m-%d %H:%M:%S')

            user_uid = log_data.get('userUid') or log_data.get('adminUid') or log_data.get('targetUserUid')

            if user_uid:

                if user_uid not in user_cache:

                    user_doc = db.collection('users').document(user_uid).get()

                    user_cache[user_uid] = f"{user_doc.to_dict().get('name', user_uid)} ({user_doc.to_dict().get('email', '')})\n{user_doc.to_dict().get('hospital', 'N/A')}" if user_doc.exists else user_uid

                log_data['user_display'] = user_cache[user_uid]

            logs.append(log_data)

        return jsonify({"logs": logs}), 200

    except Exception as e:

        app.logger.error(f"Error loading audit logs: {str(e)}", exc_info=True)

        if sentry_sdk_configured:

            sentry_sdk.capture_exception(e)

        return jsonify({'message': str(e)}), 500



def fetch_data_for_period(hospital_id, start_date, end_date):

    data_points, months_to_check = {}, set()

    current_date = start_date

    while current_date <= end_date:

        months_to_check.add(current_date.strftime("Month_%Y-%m"))

        current_date += timedelta(days=1)

    

    for month_doc_id in months_to_check:

        month_doc = db.collection("linac_data").document(hospital_id).collection("months").document(month_doc_id).get()

        if not month_doc.exists: continue

        

        for row in month_doc.to_dict().get("data_output", []):

            energy = row.get("energy")

            if energy not in data_points: data_points[energy] = []

            for i, value in enumerate(row.get("values", [])):

                try:

                    point_date = datetime.strptime(f"{month_doc_id.replace('Month_', '')}-{i+1}", "%Y-%m-%d")

                    if start_date <= point_date <= end_date and value not in [None, '']:

                        data_points[energy].append(float(value))

                except (ValueError, TypeError): continue

    return data_points



@app.route('/admin/service-impact-analysis', methods=['GET'])

def get_service_impact_analysis():

    token = request.headers.get("Authorization", "").split("Bearer ")[-1]

    is_admin, _ = verify_admin_token(token)

    if not is_admin: return jsonify({'message': 'Unauthorized'}), 403



    hospital_id = request.args.get('hospitalId')

    if not hospital_id: return jsonify({'message': 'hospitalId is required'}), 400



    try:

        users_query = db.collection('users').where('hospital', '==', hospital_id).limit(1).stream()

        user_uid = next((user.id for user in users_query), None)

        if not user_uid: return jsonify([])



        analysis_results = []

        for event in db.collection('service_events').document(user_uid).collection('events').stream():

            service_date = datetime.strptime(event.id, "%Y-%m-%d")

            before_data = fetch_data_for_period(hospital_id, service_date - timedelta(days=14), service_date - timedelta(days=1))

            after_data = fetch_data_for_period(hospital_id, service_date + timedelta(days=1), service_date + timedelta(days=14))



            for energy in set(before_data.keys()) | set(after_data.keys()):

                before_values, after_values = before_data.get(energy, []), after_data.get(energy, [])

                if not before_values or not after_values: continue



                before_std, after_std = np.std(before_values), np.std(after_values)

                improvement = ((before_std - after_std) / before_std) * 100 if before_std > 0 else 0

                

                analysis_results.append({

                    "service_date": event.id, "energy": energy,

                    "before_metrics": {"std_deviation": before_std},

                    "after_metrics": {"std_deviation": after_std},

                    "stability_improvement_percent": improvement,

                    "before_data": before_values, "after_data": after_values

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
