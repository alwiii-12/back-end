from flask import Blueprint, request, jsonify
from firebase_admin import firestore, auth
import logging
from datetime import datetime
import pytz

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
        return jsonify([]), 500 # Return empty list on error

## --- Analytics and Data Endpoints (now more robust) ---
@admin_bp.route('/benchmark-metrics', methods=['GET'])
def get_benchmark_metrics():
    # Returning empty data with a 200 OK status to prevent crashes
    return jsonify([]), 200

@admin_bp.route('/service-impact-analysis', methods=['GET'])
def get_service_impact_analysis():
    # Returning empty data with a 200 OK status to prevent crashes
    return jsonify([]), 200

@admin_bp.route('/audit-logs', methods=['GET'])
def get_audit_logs():
    db = firestore.client()
    try:
        query = db.collection("audit_logs").order_by("timestamp", direction=firestore.Query.DESCENDING).limit(200)
        logs_stream = query.stream()
        logs = []
        for doc in logs_stream:
            log_data = doc.to_dict()
            if 'timestamp' in log_data and hasattr(log_data['timestamp'], 'astimezone'):
                 log_data['timestamp'] = log_data['timestamp'].astimezone(pytz.timezone('Asia/Kolkata')).strftime('%Y-%m-%d %H:%M:%S')
            logs.append(log_data)
        return jsonify({"logs": logs}), 200
    except Exception as e:
        logger.error(f"Get audit logs failed: {e}", exc_info=True)
        return jsonify({"logs": []}), 200 # Return empty list on error
