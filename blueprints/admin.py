from flask import Blueprint, request, jsonify
from firebase_admin import firestore, auth
import logging
from datetime import datetime, timedelta
import pytz
import numpy as np
from calendar import monthrange

logger = logging.getLogger(__name__)
admin_bp = Blueprint('admin_bp', __name__, url_prefix='/admin')

verify_admin_token_wrapper = None

# Security check is enabled and handles CORS
@admin_bp.before_request
def before_request_func():
    if request.method == 'OPTIONS':
        return None
    
    auth_header = request.headers.get("Authorization")
    if not auth_header or "Bearer " not in auth_header:
        return jsonify({'message': 'Authorization header is missing or invalid'}), 401
        
    token = auth_header.split("Bearer ")[-1]
    
    if verify_admin_token_wrapper:
        is_admin, _ = verify_admin_token_wrapper(token)
        if not is_admin:
            return jsonify({'message': 'Unauthorized'}), 403
    else:
        return jsonify({'message': 'Server configuration error'}), 500

## --- User Management ---

@admin_bp.route('/users', methods=['GET'])
def get_all_users():
    db = firestore.client()
    try:
        users_stream = db.collection("users").stream()
        users_list = [doc.to_dict() | {"uid": doc.id} for doc in users_stream]
        return jsonify(users_list), 200
    except Exception as e:
        logger.error(f"Get all users failed: {e}", exc_info=True)
        return jsonify({'message': 'Failed to retrieve users.'}), 500

@admin_bp.route('/update-user', methods=['POST'])
def update_user_status():
    db = firestore.client()
    try:
        content = request.get_json(force=True)
        uid = content.get("uid")
        new_status = content.get("status")
        new_role = content.get("role")
        new_hospital = content.get("hospital")

        if not uid:
            return jsonify({'message': 'UID is required'}), 400
        
        updates = {}
        if new_status in ["active", "pending", "rejected"]:
            updates["status"] = new_status
        if new_role in ["Medical physicist", "RSO", "Admin"]:
            updates["role"] = new_role
        if new_hospital and new_hospital.strip():
            updates["hospital"] = new_hospital
            updates["centerId"] = new_hospital.lower().replace(" ", "_")

        if not updates:
            return jsonify({'message': 'No valid fields for update'}), 400

        db.collection("users").document(uid).update(updates)
        return jsonify({'status': 'success'}), 200
    except Exception as e:
        logger.error(f"Update user failed: {e}", exc_info=True)
        return jsonify({'message': str(e)}), 500

@admin_bp.route('/delete-user', methods=['DELETE'])
def delete_user():
    db = firestore.client()
    try:
        uid_to_delete = request.get_json(force=True).get("uid")
        if not uid_to_delete:
            return jsonify({'message': 'Missing UID'}), 400
        
        # Delete from Firebase Authentication first
        try:
            auth.delete_user(uid_to_delete)
        except auth.UserNotFoundError:
            logger.warning(f"User {uid_to_delete} not in Auth, deleting from Firestore only.")

        # Delete from Firestore database
        db.collection("users").document(uid_to_delete).delete()
        return jsonify({'status': 'success'}), 200
    except Exception as e:
        logger.error(f"Delete user failed: {e}", exc_info=True)
        return jsonify({'message': str(e)}), 500

@admin_bp.route('/audit-logs', methods=['GET'])
def get_audit_logs():
    db = firestore.client()
    try:
        query = db.collection("audit_logs").order_by("timestamp", direction=firestore.Query.DESCENDING)
        logs = [doc.to_dict() for doc in query.limit(200).stream()]
   
        for log in logs:
            if 'timestamp' in log and hasattr(log['timestamp'], 'astimezone'):
                 log['timestamp'] = log['timestamp'].astimezone(pytz.timezone('Asia/Kolkata')).strftime('%Y-%m-%d %H:%M:%S')

        return jsonify({"logs": logs}), 200
    except Exception as e:
        logger.error(f"Get audit logs failed: {e}", exc_info=True)
        return jsonify({'message': str(e)}), 500

## --- Data Access & Analytics ---

@admin_bp.route('/hospital-data', methods=['GET'])
def get_hospital_data():
    db = firestore.client()
    try:
        hospital_id = request.args.get('hospitalId')
        month_str = request.args.get('month')
        
        if not all([hospital_id, month_str]):
            return jsonify({'error': 'Missing required parameters'}), 400

        center_id = hospital_id.lower().replace(" ", "_")
        month_doc_ref = db.collection("linac_data").document(center_id).collection("months").document(f"Month_{month_str}")
        month_doc = month_doc_ref.get()

        if not month_doc.exists:
            return jsonify({'data': {'output': [], 'flatness': [], 'inline': [], 'crossline': []}}), 200

        return jsonify({'data': month_doc.to_dict()}), 200
    except Exception as e:
        logger.error(f"Admin fetch hospital data failed: {e}", exc_info=True)
        return jsonify({'message': str(e)}), 500

# (Placeholder endpoints for other admin features to prevent 404 errors)
@admin_bp.route('/benchmark-metrics', methods=['GET'])
def get_benchmark_metrics():
    # This is a placeholder. You would build this out with real data queries.
    return jsonify([]), 200

@admin_bp.route('/service-impact-analysis', methods=['GET'])
def get_service_impact_analysis():
    # This is a placeholder. You would build this out with real data queries.
    return jsonify([]), 200
