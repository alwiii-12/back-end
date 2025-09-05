from flask import Blueprint, request, jsonify
from firebase_admin import firestore
import logging
from calendar import monthrange
import json

# Get the database instance and a logger
logger = logging.getLogger(__name__)

# All routes in this file will be prefixed with /data
data_bp = Blueprint('data_bp', __name__, url_prefix='/data')

# Constants can be shared or redefined here
ENERGY_TYPES = ["6X", "10X", "15X", "6X FFF", "10X FFF", "6E", "9E", "12E", "15E", "18E"]
DATA_TYPES = ["output", "flatness", "inline", "crossline"]

# A placeholder for the email function, which we will connect from the main app
def send_notification_email(recipient_email, subject, body):
    # This will be replaced by the real function from app.py
    logger.info(f"--- MOCK EMAIL to {recipient_email} --- \nSubject: {subject}\nBody: {body}")
    return True

@data_bp.route('/save-annotation', methods=['POST'])
def save_annotation():
    db = firestore.client()
    try:
        content = request.get_json(force=True)
        uid, month, key, data = content.get("uid"), content.get("month"), content.get("key"), content.get("data")
        if not all([uid, month, key, data]):
            return jsonify({'status': 'error', 'message': 'Missing required fields'}), 400

        db.collection('annotations').document(uid).collection(month).document(key).set(data)
        
        if data.get('eventDate'):
            event_ref = db.collection('service_events').document(uid).collection('events').document(data['eventDate'])
            if data.get('isServiceEvent', False):
                event_ref.set({'description': data.get('text', 'Service/Calibration'), 'energy': data.get('energy'), 'dataType': data.get('dataType')})
            else:
                event_ref.delete()
        
        return jsonify({'status': 'success'}), 200
    except Exception as e:
        logger.error(f"Save annotation error: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@data_bp.route('/delete-annotation', methods=['POST'])
def delete_annotation():
    db = firestore.client()
    try:
        content = request.get_json(force=True)
        uid, month, key = content.get("uid"), content.get("month"), content.get("key")
        if not all([uid, month, key]):
            return jsonify({'status': 'error', 'message': 'Missing required fields'}), 400
        
        db.collection('annotations').document(uid).collection(month).document(key).delete()

        event_date = key.split('-', 1)[1]
        if event_date:
            db.collection('service_events').document(uid).collection('events').document(event_date).delete()

        return jsonify({'status': 'success'}), 200
    except Exception as e:
        logger.error(f"Delete annotation error: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@data_bp.route('/save', methods=['POST'])
def save_data():
    db = firestore.client()
    try:
        content = request.get_json(force=True)
        uid, month_param, raw_data, data_type = content.get("uid"), content.get("month"), content.get("data"), content.get("dataType")

        if data_type not in DATA_TYPES: return jsonify({'status': 'error', 'message': 'Invalid dataType'}), 400

        user_doc = db.collection('users').document(uid).get()
        if not user_doc.exists: return jsonify({'status': 'error', 'message': 'User not found'}), 404
        
        user_data = user_doc.to_dict()
        if user_data.get("status") != "active": return jsonify({'status': 'error', 'message': 'Account not active'}), 403
        
        center_id = user_data.get("centerId")
        if not center_id: return jsonify({'status': 'error', 'message': 'Missing centerId'}), 400

        converted = [{"energy": row[0], "values": row[1:]} for row in raw_data if len(row) > 1]
        doc_ref = db.collection("linac_data").document(center_id).collection("months").document(f"Month_{month_param}")
        doc_ref.set({f"data_{data_type}": converted}, merge=True)
        
        return jsonify({'status': 'success'}), 200
    except Exception as e:
        logger.error(f"Save data error: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500

# FIX: Corrected the route from '/fetch' to match the frontend request
@data_bp.route('/fetch', methods=['GET'])
def get_data():
    db = firestore.client()
    try:
        month_param, uid, data_type = request.args.get('month'), request.args.get('uid'), request.args.get('dataType')
        if not all([month_param, uid, data_type]): return jsonify({'error': 'Missing parameters'}), 400
        if data_type not in DATA_TYPES: return jsonify({'error': 'Invalid dataType'}), 400

        user_doc = db.collection("users").document(uid).get()
        if not user_doc.exists: return jsonify({'error': 'User not found'}), 404
        
        user_data = user_doc.to_dict()
        if user_data.get("status") != "active": return jsonify({'error': 'Account not active'}), 403

        center_id = user_data.get("centerId")
        if not center_id: return jsonify({'error': 'Missing centerId'}), 400

        year, mon = map(int, month_param.split("-"))
        num_days = monthrange(year, mon)[1]
        energy_dict = {e: [""] * num_days for e in ENERGY_TYPES}
        
        doc = db.collection("linac_data").document(center_id).collection("months").document(f"Month_{month_param}").get()
        if doc.exists:
            for row in doc.to_dict().get(f"data_{data_type}", []):
                if row.get("energy") in energy_dict:
                    energy_dict[row["energy"]] = (row.get("values", []) + [""] * num_days)[:num_days]

        return jsonify({'data': [[e] + energy_dict[e] for e in ENERGY_TYPES]}), 200
    except Exception as e:
        logger.error(f"Get data error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500

@data_bp.route('/send-alert', methods=['POST'])
def send_alert():
    db = firestore.client()
    try:
        content = request.get_json(force=True)
        uid = content.get("uid")
        user_doc = db.collection('users').document(uid).get()
        if not user_doc.exists: return jsonify({'status': 'error', 'message': 'User not found'}), 404
        
        center_id = user_doc.to_dict().get('centerId')
        if not center_id: return jsonify({'status': 'error', 'message': 'Center ID not found'}), 400

        rso_users = db.collection('users').where('centerId', '==', center_id).where('role', '==', 'RSO').stream()
        rso_emails = [rso.to_dict()['email'] for rso in rso_users if 'email' in rso.to_dict()]
        
        if not rso_emails: return jsonify({'status': 'no_rso_email'}), 200
        
        data_type = content.get("dataType", "output")
        month_key = content.get("month")
        alerts_ref = db.collection("linac_alerts").document(center_id).collection("months").document(f"Month_{month_key}_{data_type}")
        
        current_values = set(json.dumps(val, sort_keys=True) for val in content.get("outValues", []))
        prev_values_doc = alerts_ref.get()
        prev_values = set(json.dumps(val, sort_keys=True) for val in prev_values_doc.to_dict().get("alerted_values", [])) if prev_values_doc.exists else set()

        if current_values == prev_values:
            return jsonify({'status': 'no_change'})

        hospital = content.get("hospitalName", "Unknown")
        data_type_display = data_type.replace("_", " ").title()
        message_body = f"{data_type_display} QA Status for {hospital} ({month_key})\n\n"
        
        if content.get("outValues"):
            message_body += f"Out-of-Tolerance Values (±{content.get('tolerance', 2.0)}%):\n"
            for v in sorted(content.get("outValues"), key=lambda x: (x.get('energy'), x.get('date'))):
                message_body += f" - Energy: {v.get('energy')}, Date: {v.get('date')}, Value: {v.get('value')}%\n"
        else:
            message_body += f"All previously detected issues for {data_type_display} are resolved.\n"

        if send_notification_email(", ".join(rso_emails), f"⚠ {data_type_display} QA Status - {hospital}", message_body):
            alerts_ref.set({"alerted_values": content.get("outValues", [])})
            return jsonify({'status': 'alert sent'}), 200
        else:
            return jsonify({'status': 'email_send_error'}), 500
    except Exception as e:
        logger.error(f"Send alert error: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500
