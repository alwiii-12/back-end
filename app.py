# --- [IMPORTS] ---
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
import json
import logging
from calendar import monthrange
from datetime import datetime
import firebase_admin
from firebase_admin import credentials, firestore, auth

# New imports for Excel export and updated CORS
import pandas as pd
from io import BytesIO
import re # Added for CORS regular expression

app = Flask(__name__)

# Explicitly configure CORS to allow your frontend origin
# --- UPDATED CORS CONFIGURATION ---
# This new configuration uses a regular expression for the origin, which is more reliable,
# and explicitly allows the "Authorization" header required for your admin routes.
CORS(app,
     origins=[re.compile(r"https?://front-endnew\.onrender\.com")],
     supports_credentials=True,
     resources={r"/*": {}},
     allow_headers=["Authorization", "Content-Type"]
)


app.logger.setLevel(logging.DEBUG)

# --- [EMAIL CONFIG] ---
SENDER_EMAIL = os.environ.get('SENDER_EMAIL', 'itsmealwin12@gmail.com')
RECEIVER_EMAIL = os.environ.get('RECEIVER_EMAIL', 'alwinjose812@gmail.com')
APP_PASSWORD = os.environ.get('EMAIL_APP_PASSWORD')

# --- [EMAIL SENDER FUNCTION] ---
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
        missing = [f for f in required if f not in user or not user.get(f,'').strip()]
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
        app.logger.error("Login failed: %s", str(e), exc_info=True)
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
        app.logger.error("Save data failed: %s", str(e), exc_info=True)
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
        app.logger.error("Get data failed: %s", str(e), exc_info=True)
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
            return jsonify({'status': 'error', 'message': 'Missing UID or month'}), 400

        user_doc = db.collection('users').document(uid).get()
        if not user_doc.exists:
            return jsonify({'status': 'error', 'message': 'User not found'}), 404
        user_data = user_doc.to_dict()
        center_id = user_data.get('centerId')

        if not center_id:
            return jsonify({'status': 'error', 'message': 'Center ID not found for user'}), 400
            
        rso_users_query = db.collection('users').where('centerId', '==', center_id).where('role', '==', 'RSO').stream()
        rso_emails = [user.to_dict().get('email') for user in rso_users_query if user.to_dict().get('email')]
        
        if not rso_emails:
            app.logger.warning(f"No RSO email found for centerId: {center_id}. Alert not sent to RSO.")
            return jsonify({'status': 'no_rso_email', 'message': 'No RSO email found for this hospital.'}), 200

        if not APP_PASSWORD:
            app.logger.warning("APP_PASSWORD not configured. Cannot send email.")
            return jsonify({'status': 'email_credentials_missing', 'message': 'Email credentials missing'}), 500

        month_alerts_doc_ref = db.collection("linac_alerts").document(center_id).collection("months").document(f"Month_{month_key}")
        alerts_doc_snap = month_alerts_doc_ref.get()
        
        previously_alerted = alerts_doc_snap.to_dict().get("alerted_values", []) if alerts_doc_snap.exists else []
        
        previously_alerted_strings = set(json.dumps(val, sort_keys=True) for val in previously_alerted)
        current_out_values_strings = set(json.dumps(val, sort_keys=True) for val in current_out_values)

        if current_out_values_strings == previously_alerted_strings:
            return jsonify({'status': 'no_change', 'message': 'No changes to alert status.'})

        message_body = f"LINAC QA Status Update for {hospital} ({month_key})\n\n"
        if current_out_values:
            message_body += "Current Out-of-Tolerance Values (>\u00b12.0%):\n"
            for v in sorted(current_out_values, key=lambda x: (x.get('date'), x.get('energy'))):
                message_body += f"- Energy: {v.get('energy', 'N/A')}, Date: {v.get('date', 'N/A')}, Value: {v.get('value', 'N/A')}%\n"
        elif previously_alerted:
            message_body += "All previously detected LINAC QA issues for this month are now resolved.\n"
        else:
            message_body += "All LINAC QA values are currently within tolerance.\n"
        
        if send_notification_email(", ".join(rso_emails), f"LINAC QA Status - {hospital} ({month_key})", message_body):
            month_alerts_doc_ref.set({"alerted_values": current_out_values})
            return jsonify({'status': 'alert sent', 'message': 'Email sent and alert state updated.'}), 200
        else:
            return jsonify({'status': 'email_send_error', 'message': 'Failed to send email notification.'}), 500

    except Exception as e:
        app.logger.error(f"Error in send_alert function: {str(e)}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500

# --- CHATBOT QUERY ENDPOINT ---
@app.route('/query-qa-data', methods=['POST'])
def query_qa_data():
    try:
        content = request.get_json(force=True)
        query_type = content.get("query")
        month_param = content.get("month")
        uid = content.get("uid")
        
        if not all([query_type, month_param, uid]):
            return jsonify({'status': 'error', 'message': 'Missing required parameters'}), 400

        user_doc = db.collection("users").document(uid).get()
        if not user_doc.exists: return jsonify({'status': 'error', 'message': 'User not found'}), 404
        center_id = user_doc.to_dict().get("centerId")
        if not center_id: return jsonify({'status': 'error', 'message': 'Missing centerId for user'}), 400

        doc_ref = db.collection("linac_data").document(center_id).collection("months").document(f"Month_{month_param}")
        data_rows = doc_ref.get().to_dict().get("data", []) if doc_ref.get().exists else []

        year, mon = map(int, month_param.split("-"))
        date_strings = [f"{year}-{mon:02d}-{day:02d}" for day in range(1, monthrange(year, mon)[1] + 1)]

        if query_type == "out_of_tolerance_dates":
            out_dates = set()
            for row in data_rows:
                for i, value in enumerate(row.get("values", [])):
                    if i < len(date_strings) and isinstance(value, (int, float)) and abs(value) > 2.0:
                        out_dates.add(date_strings[i])
            return jsonify({'status': 'success', 'dates': sorted(list(out_dates))}), 200

        # ... other query types ...

        else:
            return jsonify({'status': 'error', 'message': 'Unknown query type'}), 400

    except Exception as e:
        app.logger.error(f"Chatbot query failed: {str(e)}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500

# --- ADMIN: GET ALL USERS ---
@app.route('/admin/users', methods=['GET'])
async def get_all_users():
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_admin, _ = await verify_admin_token(token)
    if not is_admin:
        return jsonify({'message': 'Unauthorized'}), 403

    try:
        users_stream = db.collection("users").stream()
        all_users = [doc.to_dict() | {"uid": doc.id} for doc in users_stream]
        return jsonify(all_users), 200
    except Exception as e:
        app.logger.error(f"Error loading all users: {str(e)}", exc_info=True)
        return jsonify({'message': str(e)}), 500

# --- ADMIN: UPDATE USER ---
@app.route('/admin/update-user-status', methods=['POST'])
async def update_user_status():
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_admin, _ = await verify_admin_token(token)
    if not is_admin:
        return jsonify({'message': 'Unauthorized'}), 403
    try:
        content = request.get_json(force=True)
        uid = content.get("uid")
        if not uid: return jsonify({'message': 'UID is required'}), 400

        updates = {k: v for k, v in content.items() if k in ["status", "role", "hospital"] and v}
        if "hospital" in updates: updates["centerId"] = updates["hospital"]
        
        if not updates: return jsonify({'message': 'No valid fields provided for update'}), 400
        
        ref = db.collection("users").document(uid)
        ref.update(updates)

        updated_user_data = ref.get().to_dict()
        if APP_PASSWORD and updated_user_data.get("email"):
            subject = "LINAC QA Account Update"
            body = f"Your LINAC QA account details have been updated by an administrator.\n\n"
            if "status" in updates: body += f"New Status: {updates['status'].upper()}\n"
            if "role" in updates: body += f"New Role: {updates['role']}\n"
            if "hospital" in updates: body += f"New Hospital: {updates['hospital']}\n"
            send_notification_email(updated_user_data["email"], subject, body)

        return jsonify({'status': 'success', 'message': 'User updated successfully'}), 200
    except Exception as e:
        app.logger.error(f"Error updating user: {str(e)}", exc_info=True)
        return jsonify({'message': str(e)}), 500

# --- ADMIN: DELETE USER ---
@app.route('/admin/delete-user', methods=['DELETE'])
async def delete_user():
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_admin, _ = await verify_admin_token(token)
    if not is_admin:
        return jsonify({'message': 'Unauthorized'}), 403

    try:
        uid_to_delete = request.get_json(force=True).get("uid")
        if not uid_to_delete: return jsonify({'message': 'Missing UID for deletion'}), 400

        auth.delete_user(uid_to_delete)
        db.collection("users").document(uid_to_delete).delete()
        
        app.logger.info(f"Successfully deleted user {uid_to_delete} from Auth and Firestore.")
        return jsonify({'status': 'success', 'message': 'User deleted successfully'}), 200
    except Exception as e:
        app.logger.error(f"Error deleting user {uid_to_delete}: {str(e)}", exc_info=True)
        return jsonify({'message': f"Failed to delete user: {str(e)}"}), 500
        
# --- ADMIN: GET HOSPITAL DATA ---
@app.route('/admin/hospital-data', methods=['GET'])
async def get_hospital_data():
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_admin, _ = await verify_admin_token(token)
    if not is_admin:
        return jsonify({'message': 'Unauthorized'}), 403
        
    hospital_id = request.args.get('hospitalId')
    month_param = request.args.get('month')
    
    if not all([hospital_id, month_param]):
        return jsonify({'message': 'hospitalId and month parameters are required'}), 400
        
    try:
        # Since hospitalId is the centerId
        doc_ref = db.collection("linac_data").document(hospital_id).collection("months").document(f"Month_{month_param}")
        doc = doc_ref.get()
        
        if not doc.exists:
            return jsonify({'message': 'No data found for this hospital and month'}), 404
            
        data = doc.to_dict().get("data", [])
        # Reformat data back to the simple list-of-lists format for the frontend table
        table_data = [[row.get('energy')] + row.get('values', []) for row in data]
        return jsonify({'data': table_data}), 200
        
    except Exception as e:
        app.logger.error(f"Error fetching hospital data for {hospital_id}: {str(e)}", exc_info=True)
        return jsonify({'message': str(e)}), 500

# --- INDEX ---
@app.route('/')
def index():
    return "✅ LINAC QA Backend Running"

# --- RUN ---
if __name__ == '__main__':
    app.run(debug=True)
