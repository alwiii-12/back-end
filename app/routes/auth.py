from flask import Blueprint, jsonify, request
from app.services.firebase import db, auth_module
from firebase_admin import firestore
import logging

# All routes in this file will be at the root level (e.g., /signup)
bp = Blueprint('auth', __name__)

@bp.route('/signup', methods=['POST'])
def signup():
    """Handles new user registration."""
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
        logging.error(f"Signup failed: {str(e)}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@bp.route('/login', methods=['POST'])
def login():
    """Handles user login and status verification."""
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
        logging.error(f"Login failed: {str(e)}", exc_info=True)
        return jsonify({'status': 'error', 'message': 'An internal server error occurred during login.'}), 500

@bp.route('/update-profile', methods=['POST'])
def update_profile():
    """Allows a user to update their own profile information."""
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
        
        logging.info(f"User {uid} updated their profile.")
        return jsonify({'status': 'success', 'message': 'Profile updated successfully'}), 200

    except Exception as e:
        logging.error(f"Profile update failed: {str(e)}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@bp.route('/log_event', methods=['POST'])
def log_event():
    """Logs a generic event, like user logout, to the audit trail."""
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
            "hospital": user_data.get("hospital", "N/A"),
            "details": {
                "user_email": user_data.get("email", "N/A"),
                "user_agent": request.headers.get('User-Agent')
            }
        }
        db.collection("audit_logs").add(audit_entry)
        
        return jsonify({'status': 'success', 'message': 'Event logged'}), 200
    except Exception as e:
        logging.error(f"Error logging event: {str(e)}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500
