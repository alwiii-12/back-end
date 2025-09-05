from flask import Blueprint, request, jsonify
from firebase_admin import firestore, auth
import logging
from datetime import datetime, timedelta
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
        return jsonify({'message': 'Failed to retrieve users.'}), 500

## --- Placeholder Endpoints to Prevent 404/500 Errors ---
@admin_bp.route('/benchmark-metrics', methods=['GET'])
def get_benchmark_metrics():
    # Returning empty data with a 200 OK status
    return jsonify([]), 200

@admin_bp.route('/service-impact-analysis', methods=['GET'])
def get_service_impact_analysis():
    # Returning empty data with a 200 OK status
    return jsonify([]), 200

@admin_bp.route('/audit-logs', methods=['GET'])
def get_audit_logs():
    db = firestore.client()
    try:
        # Returning empty logs for now to ensure the page loads
        return jsonify({"logs": []}), 200
    except Exception as e:
        logger.error(f"Get audit logs failed: {e}", exc_info=True)
        return jsonify({'message': str(e)}), 500
