from flask import Blueprint, request, jsonify
from firebase_admin import firestore, auth
import logging
from datetime import datetime, timedelta
import pytz
import numpy as np

logger = logging.getLogger(__name__)
admin_bp = Blueprint('admin_bp', __name__, url_prefix='/admin')

# This helper will be connected from the main app
verify_admin_token_wrapper = None

@admin_bp.before_request
def before_request_func():
    # FIX: Explicitly allow all OPTIONS requests for CORS preflight
    if request.method == 'OPTIONS':
        return None # Let Flask-Cors handle the response

    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    
    # Ensure the wrapper has been connected from app.py
    if verify_admin_token_wrapper:
        is_admin, _ = verify_admin_token_wrapper(token)
        if not is_admin:
            return jsonify({'message': 'Unauthorized'}), 403
    else:
        # Failsafe if the wrapper isn't connected
        return jsonify({'message': 'Server configuration error'}), 500

## --- User Management Endpoints ---

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
def update_user():
    db = firestore.client()
    try:
        content = request.get_json(force=True)
        uid = content.get("uid")
        if not uid:
            return jsonify({'message': 'User UID is required'}), 400

        updates = {}
        if "status" in content and content["status"] in ["active", "pending", "rejected"]:
            updates["status"] = content["status"]
        if "role" in content and content["role"] in ["Medical physicist", "RSO", "Admin"]:
            updates["role"] = content["role"]
        if "hospital" in content and content["hospital"].strip():
            hospital_name = content["hospital"].strip()
            updates["hospital"] = hospital_name
            updates["centerId"] = hospital_name.lower().replace(" ", "_")

        if not updates:
            return jsonify({'message': 'No valid fields provided for update'}), 400

        db.collection("users").document(uid).update(updates)
        return jsonify({'status': 'success', 'message': 'User updated successfully'}), 200
    except Exception as e:
        logger.error(f"Update user failed: {e}", exc_info=True)
        return jsonify({'message': 'Failed to update user.'}), 500

@admin_bp.route('/delete-user', methods=['DELETE'])
def delete_user():
    db = firestore.client()
    try:
        uid_to_delete = request.get_json(force=True).get("uid")
        if not uid_to_delete:
            return jsonify({'message': 'Missing UID for deletion'}), 400
        
        try:
            auth.delete_user(uid_to_delete)
        except auth.UserNotFoundError:
            logger.warning(f"User {uid_to_delete} not in Auth, deleting from Firestore.")
        
        db.collection("users").document(uid_to_delete).delete()
        return jsonify({'status': 'success', 'message': 'User deleted successfully'}), 200
    except Exception as e:
        logger.error(f"Delete user failed: {e}", exc_info=True)
        return jsonify({'message': 'An error occurred during user deletion.'}), 500

## --- Data Access & Analytics Endpoints ---

@admin_bp.route('/hospital-data', methods=['GET'])
def get_hospital_data():
    db = firestore.client()
    try:
        hospital_id = request.args.get('hospitalId')
        month_str = request.args.get('month')
        
        if not all([hospital_id, month_str]):
            return jsonify({'error': 'Missing hospitalId or month parameter'}), 400

        center_id = hospital_id.lower().replace(" ", "_")
        month_doc_ref = db.collection("linac_data").document(center_id).collection("months").document(f"Month_{month_str}")
        month_doc = month_doc_ref.get()

        if not month_doc.exists:
            return jsonify({'data': {}}), 200

        return jsonify({'data': month_doc.to_dict()}), 200
    except Exception as e:
        logger.error(f"Admin fetch hospital data failed: {e}", exc_info=True)
        return jsonify({'message': str(e)}), 500

def calculate_stability_metrics(data_points):
    if not data_points or len(data_points) < 2:
        return 0.0
    valid_points = [float(p) for p in data_points if p is not None and isinstance(p, (int, float, str)) and str(p).replace('.', '', 1).isdigit()]
    if len(valid_points) < 2:
        return 0.0
    return np.std(valid_points)

@admin_bp.route('/service-impact-analysis', methods=['GET'])
def get_service_impact_analysis():
    db = firestore.client()
    try:
        center_ids = ["aoi_gurugram", "medanta_gurugram", "fortis_delhi", "apollo_chennai", "max_delhi"]
        data_types = ["output", "flatness", "inline", "crossline"]
        all_results = []

        for center_id in center_ids:
            users_ref = db.collection('users').where('centerId', '==', center_id).limit(1).stream()
            user_uid = next((user.id for user in users_ref), None)
            
            if not user_uid: continue

            service_events_ref = db.collection('service_events').document(user_uid).collection('events').stream()
            
            for event in service_events_ref:
                service_date = datetime.strptime(event.id, '%Y-%m-%d')
                event_analysis = {"hospital": center_id, "service_date": event.id, "analysis": {}}
                before_start = service_date - timedelta(days=30)
                after_end = service_date + timedelta(days=30)
                
                all_data_docs = db.collection("linac_data").document(center_id).collection("months").stream()
                center_data = {}
                for doc in all_data_docs:
                    month_data = doc.to_dict()
                    for key, values in month_data.items():
                        data_type = key.replace('data_', '')
                        if data_type not in center_data: center_data[data_type] = {}
                        for row in values:
                            energy = row.get('energy')
                            if energy not in center_data[data_type]: center_data[data_type][energy] = []
                            month_str = doc.id.replace("Month_", "")
                            year, mon = map(int, month_str.split("-"))
                            for i, val in enumerate(row.get('values', [])):
                                if val:
                                    try:
                                        current_date = datetime(year, mon, i + 1)
                                        center_data[data_type][energy].append({'date': current_date, 'value': val})
                                    except ValueError: continue
                
                for data_type in data_types:
                    energy_data = center_data.get(data_type, {}).get('6X', [])
                    data_before = [d['value'] for d in energy_data if before_start <= d['date'] < service_date]
                    data_after = [d['value'] for d in energy_data if service_date < d['date'] <= after_end]
                    
                    before_std = calculate_stability_metrics(data_before)
                    after_std = calculate_stability_metrics(data_after)
                    
                    improvement = 0.0
                    if before_std > 0.0001:
                        improvement = ((before_std - after_std) / before_std) * 100.0
                    elif after_std > 0:
                        improvement = -999.0
                    
                    event_analysis["analysis"][data_type] = {
                        "before_std": before_std, "after_std": after_std, "stability_improvement_percent": improvement
                    }
                all_results.append(event_analysis)
        return jsonify(all_results), 200
    except Exception as e:
        logger.error(f"Service impact analysis failed: {e}", exc_info=True)
        return jsonify({'message': 'An error occurred during analysis.'}), 500

@admin_bp.route('/audit-logs', methods=['GET'])
def get_audit_logs():
    db = firestore.client()
    try:
        query = db.collection("audit_logs").order_by("timestamp", direction=firestore.Query.DESCENDING)
        if request.args.get('hospitalId'):
            query = query.where('hospital', '==', request.args.get('hospitalId'))
        if request.args.get('action'):
            query = query.where('action', '==', request.args.get('action'))
        if request.args.get('date'):
            start_date = datetime.strptime(request.args.get('date'), '%Y-%m-%d')
            end_date = start_date + timedelta(days=1)
            query = query.where('timestamp', '>=', start_date).where('timestamp', '<', end_date)

        logs_stream = query.limit(200).stream()
        logs = []
        for doc in logs_stream:
            log_data = doc.to_dict()
            if 'timestamp' in log_data and hasattr(log_data['timestamp'], 'astimezone'):
                log_data['timestamp'] = log_data['timestamp'].astimezone(pytz.timezone('Asia/Kolkata')).strftime('%Y-%m-%d %H:%M:%S')
            logs.append(log_data)

        return jsonify({"logs": logs}), 200
    except Exception as e:
        logger.error(f"Get audit logs failed: {e}", exc_info=True)
        return jsonify({'message': 'Failed to retrieve audit logs.'}), 500
