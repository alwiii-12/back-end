import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from calendar import monthrange
import firebase_admin
from firebase_admin import credentials, auth, firestore
from flask import Flask, request, jsonify
from flask_cors import CORS

# Initialize Firebase Admin SDK
# Use service account key from environment variable
# It's safer to store the JSON content in a string environment variable
# and then load it.
try:
    FIREBASE_SERVICE_ACCOUNT_KEY = os.environ.get("FIREBASE_SERVICE_ACCOUNT_KEY_JSON")
    if FIREBASE_SERVICE_ACCOUNT_KEY:
        cred = credentials.Certificate(json.loads(FIREBASE_SERVICE_ACCOUNT_KEY))
    else:
        # Fallback for local development if not using env variable (e.g., direct file)
        # In production, ALWAYS use environment variables for sensitive data.
        cred = credentials.Certificate("path/to/your/serviceAccountKey.json") # <<< Replace with your actual path in local dev
    
    firebase_admin.initialize_app(cred)
except ValueError as e:
    print(f"Error loading Firebase credentials: {e}")
    print("Ensure FIREBASE_SERVICE_ACCOUNT_KEY_JSON environment variable is set and valid JSON.")
    exit(1) # Exit if Firebase initialization fails

db = firestore.client()

app = Flask(__name__)
CORS(app) # Enable CORS for all routes

# Environment variables for email alerts
SENDER_EMAIL = os.environ.get("SENDER_EMAIL")
APP_PASSWORD = os.environ.get("APP_PASSWORD") # App password for Gmail or similar
RECEIVER_EMAIL = os.environ.get("RECEIVER_EMAIL") # Admin email to receive alerts

ENERGY_TYPES = ["6X", "10X", "15X", "6X FFF", "10X FFF", "6E", "9E", "12E", "15E", "18E"]

async def verify_admin_token(token):
    try:
        decoded_token = auth.verify_id_token(token)
        uid = decoded_token['uid']
        user_doc = db.collection('users').document(uid).get()
        if user_doc.exists:
            user_data = user_doc.to_dict()
            return user_data.get('role') == 'admin', uid
        return False, None
    except Exception:
        return False, None

def send_notification_email(recipient_email, subject, body):
    if not SENDER_EMAIL or not APP_PASSWORD:
        print("Email sending skipped: SENDER_EMAIL or APP_PASSWORD not configured.")
        return False
    
    try:
        msg = MIMEMultipart()
        msg['From'] = SENDER_EMAIL
        msg['To'] = recipient_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        server = smtplib.SMTP_SSL("smtp.gmail.com", 465)
        server.login(SENDER_EMAIL, APP_PASSWORD)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        print(f"Error sending email: {e}")
        return False

# --- USER SIGNUP ---
@app.route('/signup', methods=['POST'])
async def signup():
    try:
        content = request.get_json(force=True)
        email = content.get("email")
        password = content.get("password")
        hospital_name = content.get("hospitalName")

        if not email or not password or not hospital_name:
            return jsonify({'message': 'Missing email, password, or hospital name'}), 400

        user = auth.create_user(email=email, password=password)
        
        # Determine initial role: first user is admin, subsequent are pending
        users_count = len(list(db.collection('users').stream()))
        initial_role = 'admin' if users_count == 0 else 'pending'

        # Generate a unique ID for the hospital (e.g., from hospital name)
        # For simplicity, let's use a normalized version of hospitalName
        center_id = hospital_name.replace(" ", "").lower()
        
        db.collection('users').document(user.uid).set({
            'email': email,
            'hospitalName': hospital_name,
            'centerId': center_id, # Store the generated centerId
            'role': initial_role,
            'status': 'active' if initial_role == 'admin' else 'pending'
        })

        if initial_role == 'pending':
            # Notify admin of new pending user
            admin_message = f"New user signup pending approval:\nEmail: {email}\nHospital: {hospital_name}"
            if RECEIVER_EMAIL:
                send_notification_email(RECEIVER_EMAIL, "New LINAC QA User Pending Approval", admin_message)
            else:
                print("Admin email not configured for signup notifications.")
            
            return jsonify({
                'status': 'pending',
                'message': 'Account created, awaiting admin approval.'
            }), 200
        else:
            return jsonify({
                'status': 'active',
                'message': 'Admin account created successfully!'
            }), 200

    except Exception as e:
        if 'email-already-exists' in str(e):
            return jsonify({'message': 'Email already registered.'}), 409
        return jsonify({'message': str(e)}), 500

# --- USER LOGIN ---
@app.route('/login', methods=['POST'])
async def login():
    try:
        content = request.get_json(force=True)
        id_token = content.get("idToken")
        
        # Verify the ID token using Firebase Admin SDK
        decoded_token = auth.verify_id_token(id_token)
        uid = decoded_token['uid']

        user_doc = db.collection("users").document(uid).get()
        if not user_doc.exists:
            return jsonify({'message': 'User data not found in Firestore'}), 404
        
        user_data = user_doc.to_dict()
        
        # The problematic line was likely intended to be part of the jsonify return:
        return jsonify({
            'uid': uid,
            'email': user_data.get("email"),
            'hospitalName': user_data.get("hospitalName"),
            'centerId': user_data.get("centerId", ""), # Corrected this line's indentation
            'status': user_data.get("status", "unknown") # This was the problematic line
        }), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': 'Login failed: ' + str(e)}), 500

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
def send_alert():
    try:
        content = request.get_json(force=True)
        out_values = content.get("outValues", [])
        hospital = content.get("hospitalName", "Unknown")
        if not out_values:
            return jsonify({'status': 'no alerts sent'})
        message = f"Alert from {hospital}\n\nOut-of-tolerance values (±2.0%):\n\n"
        for v in out_values:
            message += f"Energy: {v['energy']}, Date: {v['date']}, Value: {v['value']}%\n"

        msg = MIMEMultipart()
        msg['From'] = SENDER_EMAIL
        msg['To'] = RECEIVER_EMAIL
        msg['Subject'] = f"⚠ LINAC QA Alert - {hospital}"
        msg.attach(MIMEText(message, 'plain'))

        if APP_PASSWORD:
            server = smtplib.SMTP_SSL("smtp.gmail.com", 465)
            server.login(SENDER_EMAIL, APP_PASSWORD)
            server.send_message(msg)
            server.quit()
            return jsonify({'status': 'alert sent'}), 200
        else:
            return jsonify({'status': 'email not sent'}), 500
    except Exception as e:
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
        return jsonify({'message': str(e)}), 500

# --- ADMIN: UPDATE USER STATUS ---
@app.route('/admin/update-user-status', methods=['POST'])
async def update_user_status():
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_admin, admin_uid = await verify_admin_token(token)
    if not is_admin:
        return jsonify({'message': 'Unauthorized'}), 403
    try:
        content = request.get_json(force=True)
        uid = content.get("uid")
        status = content.get("status")
        if not uid or status not in ["active", "rejected"]:
            return jsonify({'message': 'Invalid input'}), 400
        ref = db.collection("users").document(uid)
        ref.update({"status": status})
        data = ref.get().to_dict()
        if APP_PASSWORD and data.get("email"):
            msg = "Your LINAC QA account has been " + ("approved." if status == "active" else "rejected.")
            send_notification_email(data["email"], "LINAC QA Status Update", msg)
        return jsonify({'status': 'success'}), 200
    except Exception as e:
        return jsonify({'message': str(e)}), 500

# --- INDEX ---
@app.route('/')
def index():
    return "✅ LINAC QA Backend Running"

# --- RUN ---
if __name__ == '__main__':
    app.run(debug=True)
