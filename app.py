# --- [UNCHANGED IMPORTS] ---
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
# RECEIVER_EMAIL will now primarily be used for other admin notifications, not all QA alerts
RECEIVER_EMAIL = os.environ.get('RECEIVER_EMAIL', 'alwinjose812@gmail.com')
APP_PASSWORD = os.environ.get('EMAIL_APP_PASSWORD')

# --- [EMAIL SENDER FUNCTION] ---
def send_notification_email(recipient_email, subject, body):
    if not APP_PASSWORD:
        app.logger.warning(f"🚫 Cannot send notification to {recipient_email}: APP_PASSWORD not configured.")
        return False
    msg = MIMultipart()
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

        rso_emails = []
        try: # Nested try for rso_emails fetching
            rso_users = db.collection('users').where('centerId', '==', center_id).where('role', '==', 'RSO').stream()
            for rso_user in rso_users:
                rso_data = rso_user.to_dict()
                if 'email' in rso_data and rso_data['email']:
                    rso_emails.append(rso_data['email'])
            
            # This return is correctly inside the nested try-except for email fetching
            if not rso_emails:
                app.logger.warning(f"No RSO email found for centerId: {center_id}. Alert not sent to RSO.")
                return jsonify({'status': 'no_rso_email', 'message': 'No RSO email found for this hospital.'}), 200

        except Exception as e:
            app.logger.error(f"Error fetching RSO emails for center {center_id}: {str(e)}", exc_info=True)
            return jsonify({'status': 'error', 'message': 'Failed to fetch RSO emails'}), 500

        # Check APP_PASSWORD upfront
        if not APP_PASSWORD:
            app.logger.warning("APP_PASSWORD not configured. Cannot send email.")
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
        msg = MIMultipart()
        msg['From'] = SENDER_EMAIL
        # MODIFIED: Send to RSO emails
        msg['To'] = ", ".join(rso_emails) # Join emails with comma for 'To' field
        msg['Subject'] = f"⚠ LINAC QA Status - {hospital} ({month_key})"
        msg.attach(MIMEText(message_body, 'plain'))

        # This part of email sending needs to be robustly handled within a try block or similar.
        # This nested try-except will specifically handle smtplib errors.
        try:
            server = smtplib.SMTP_SSL("smtp.gmail.com", 465)
            server.login(SENDER_EMAIL, APP_PASSWORD)
            server.send_message(msg)
            server.quit()
            app.logger.info(f"Email alert sent to RSO(s) {msg['To']} for {hospital} ({month_key}).")

            month_alerts_doc_ref.set({"alerted_values": current_out_values}, merge=False)
            app.logger.debug(f"Alert state updated in Firestore for {center_id}/{month_key}.")
            return jsonify({'status': 'alert sent', 'message': 'Email sent and alert state updated.'}), 200
        except Exception as email_e:
            app.logger.error(f"Error sending email via SMTPLib: {str(email_e)}", exc_info=True)
            # If email fails, return an appropriate error message related to email
            return jsonify({'status': 'email_send_error', 'message': f'Failed to send email: {str(email_e)}'}), 500

    except Exception as e: # This is the main try's except block, catching all other potential errors
        app.logger.error(f"Error in send_alert function: {str(e)}", exc_info=True)
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
        return jsonify({'status': 'error', 'message': str(e)}), 500


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
        if APP_PASSWORD and updated_user_data.get("email"):
            subject = "LINAC QA Account Update"
            body = f"Your LINAC QA account details have been updated."
            
            if "status" in updates:
                status_text = updates["status"].upper()
                body += f"\nYour account status is now: {status_text}."
                if status_text == "ACTIVE":
                    body += " You can now log in and use the portal."
                elif status_text == "REJECTED":
                    body += " Please contact us"
                elif status_text == "PENDING":
                    body += " Please contact us"
                else:
                    body += " Please contact support for more information."
            
            if "role" in updates:
                 body += f"\nYour role has been updated to: {updates['role']}."
            if "hospital" in updates:
                 body += f"\nYour hospital has been updated to: {updates['hospital']}."

            send_notification_email(updated_user_data["email"], subject, body)

        return jsonify({'status': 'success', 'message': 'User updated successfully'}), 200
    except Exception as e:
        app.logger.error(f"Error updating user status/role/hospital: {str(e)}", exc_info=True)
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
            # If user not found in Auth, might have been deleted already or never fully created
            if "User record not found" in str(e):
                app.logger.warning(f"Firebase Auth user {uid_to_delete} not found, proceeding with Firestore deletion.")
            else:
                app.logger.error(f"Error deleting Firebase Auth user {uid_to_delete}: {str(e)}", exc_info=True)
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
        
        # Optional: Delete associated QA data if desired (more complex, consider implications)
        # This would involve iterating through subcollections, which can be expensive/slow
        # Example (DO NOT USE FOR LARGE DATASETS, requires recursive delete):
        # db.collection("linac_data").document(user_data.get("centerId")).delete() 
        # This line might delete all data for a hospital if centerId is just hospital name.
        # Be very careful with this. For now, we only delete the user, not their QA data.

        return jsonify({'status': 'success', 'message': 'User deleted successfully'}), 200

    except Exception as e:
        app.logger.error(f"Error deleting user: {str(e)}", exc_info=True)
        return jsonify({'message': f"Failed to delete user: {str(e)}"}), 500

# --- NEW: Export Excel Endpoint ---
@app.route('/export-excel', methods=['POST'])
async def export_excel_data():
    try:
        content = request.get_json(force=True)
        month_param = content.get('month')
        uid = content.get('uid')

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

        # Fetch the data from Firestore (same logic as /data endpoint)
        year, mon = map(int, month_param.split("-"))
        _, num_days = monthrange(year, mon)
        
        # Prepare column headers for Excel
        date_cols = [f"{year}-{str(mon).zfill(2)}-{str(i+1).zfill(2)}" for i in range(num_days)]
        excel_column_headers = ['Energy'] + date_cols

        energy_dict = {e: [""] * num_days for e in ENERGY_TYPES}

        doc = db.collection("linac_data").document(center_id).collection("months").document(f"Month_{month_param}").get()
        if doc.exists:
            for row in doc.to_dict().get("data", []):
                energy, values = row.get("energy"), row.get("values", [])
                if energy in energy_dict:
                    # Ensure values are padded/truncated to match num_days
                    energy_dict[energy] = (values + [""] * num_days)[:num_days]

        # Construct the data for pandas DataFrame
        excel_data = []
        for energy_type in ENERGY_TYPES:
            excel_data.append([energy_type] + energy_dict[energy_type])

        # Create a Pandas DataFrame
        df = pd.DataFrame(excel_data, columns=excel_column_headers)

        # Export to Excel in-memory
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='LINAC QA Data')
        output.seek(0)

        # Send the Excel file as a response
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=f"LINAC_QA_Data_{month_param}.xlsx"
        )

    except Exception as e:
        app.logger.error(f"Error exporting Excel data: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500


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
        if APP_PASSWORD and updated_user_data.get("email"):
            subject = "LINAC QA Account Update"
            body = f"Your LINAC QA account details have been updated."
            
            if "status" in updates:
                status_text = updates["status"].upper()
                body += f"\nYour account status is now: {status_text}."
                if status_text == "ACTIVE":
                    body += " You can now log in and use the portal."
                elif status_text == "REJECTED":
                    body += " Please contact us"
                elif status_text == "PENDING":
                    body += " Please contact us"
                else:
                    body += " Please contact support for more information."
            
            if "role" in updates:
                 body += f"\nYour role has been updated to: {updates['role']}."
            if "hospital" in updates:
                 body += f"\nYour hospital has been updated to: {updates['hospital']}."

            send_notification_email(updated_user_data["email"], subject, body)

        return jsonify({'status': 'success', 'message': 'User updated successfully'}), 200
    except Exception as e:
        app.logger.error(f"Error updating user status/role/hospital: {str(e)}", exc_info=True)
        return jsonify({'message': str(e)}), 500

# --- ADMIN: GET INDIVIDUAL HOSPITAL'S QA DATA ---
@app.route('/admin/hospital-data', methods=['GET'])
async def get_hospital_qa_data():
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_admin, _ = await verify_admin_token(token)
    if not is_admin:
        return jsonify({'message': 'Unauthorized'}), 403

    hospital_id = request.args.get('hospitalId') # This will correspond to the centerId in Firestore
    month_param = request.args.get('month') # Format:YYYY-MM

    if not hospital_id or not month_param:
        return jsonify({'error': 'Missing "hospitalId" or "month" parameter'}), 400

    try:
        # Fetch the data for the specific hospital and month from linac_data collection
        doc_ref = db.collection("linac_data").document(hospital_id).collection("months").document(f"Month_{month_param}")
        doc = doc_ref.get()

        if doc.exists:
            raw_data = doc.to_dict().get("data", [])
            
            # Reconstruct the table data similar to how /data endpoint does it,
            # ensuring all energy types are present and padded for the month's days.
            year, mon = map(int, month_param.split("-"))
            _, num_days = monthrange(year, mon)
            
            energy_dict = {e: [""] * num_days for e in ENERGY_TYPES} # Use the global ENERGY_TYPES
            
            for row in raw_data:
                energy, values = row.get("energy"), row.get("values", [])
                if energy in energy_dict:
                    # Ensure values are padded/truncated to match num_days
                    energy_dict[energy] = (values + [""] * num_days)[:num_days]

            # Convert back to list of lists structure for table display
            table_data = [[e] + energy_dict[e] for e in ENERGY_TYPES]

            return jsonify({'status': 'success', 'data': table_data}), 200
        else:
            return jsonify({'status': 'success', 'data': [], 'message': 'No data found for this hospital and month.'}), 200

    except Exception as e:
        app.logger.error(f"Error fetching hospital QA data: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500

# --- INDEX ---
@app.route('/')
def index():
    return "✅ LINAC QA Backend Running"

# --- RUN ---
if __name__ == '__main__':
    app.run(debug=True)
