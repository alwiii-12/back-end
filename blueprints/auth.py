from flask import Blueprint, request, jsonify
from firebase_admin import firestore
import logging

logger = logging.getLogger(__name__)
auth_bp = Blueprint('auth_bp', __name__, url_prefix='/auth')

# Note: We will need to pass the 'get_real_ip' function from the main app.py
# For now, we define a placeholder. This will be connected in the main app factory.
def get_real_ip():
    if 'X-Forwarded-For' in request.headers:
        return request.headers['X-Forwarded-For'].split(',')[0].strip()
    return request.remote_addr

@auth_bp.route('/signup', methods=['POST'])
def signup():
    db = firestore.client()
    try:
        user = request.get_json(force=True)
        required = ['name', 'email', 'hospital', 'role', 'uid', 'status']
        missing = [f for f in required if f not in user or not user.get(f, "").strip()]
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
            'centerId': user['hospital'].lower().replace(" ", "_"),
            'status': user['status']
        })
        return jsonify({'status': 'success', 'message': 'User registered'}), 200
    except Exception as e:
        logger.error(f"Signup failed: {str(e)}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@auth_bp.route('/login', methods=['POST'])
def login():
    db = firestore.client()
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

        # --- AUDIT LOGGING ---
        audit_entry = {
            "timestamp": firestore.SERVER_TIMESTAMP,
            "action": "user_login",
            "targetUserUid": uid,
            "hospital": user_data.get("hospital", "N/A").lower().replace(" ", "_"),
            "details": {
                "user_email": user_data.get("email", "N/A"),
                "ip_address": get_real_ip(),
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
        logger.error(f"Login failed: {str(e)}", exc_info=True)
        return jsonify({'status': 'error', 'message': 'An internal server error occurred during login.'}), 500

@auth_bp.route('/update-profile', methods=['POST'])
def update_profile():
    db = firestore.client()
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
            'centerId': new_hospital.lower().replace(" ", "_")
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
        
        logger.info(f"User {uid} updated their profile.")
        return jsonify({'status': 'success', 'message': 'Profile updated successfully'}), 200

    except Exception as e:
        logger.error(f"Profile update failed: {str(e)}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@auth_bp.route('/log_event', methods=['POST'])
def log_event():
    db = firestore.client()
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
            "action": action,
            "targetUserUid": user_uid,
            "hospital": user_data.get("hospital", "N/A").lower().replace(" ", "_"),
            "details": {
                "user_email": user_data.get("email", "N/A"),
                "ip_address": get_real_ip(),
                "user_agent": request.headers.get('User-Agent')
            }
        }
        db.collection("audit_logs").add(audit_entry)
        
        return jsonify({'status': 'success', 'message': 'Event logged'}), 200
    except Exception as e:
        logger.error(f"Error logging event: {str(e)}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500
