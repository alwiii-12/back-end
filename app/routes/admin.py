from flask import Blueprint, jsonify, request
from datetime import datetime, timedelta
import pytz
import uuid
import numpy as np
from scipy import stats
import sentry_sdk
import logging

# Import custom services and modules
from app.services.firebase import db, auth_module
from firebase_admin import firestore

bp = Blueprint('admin', __name__)

# --- HELPER FUNCTIONS FOR TOKEN VERIFICATION ---

def verify_admin_token(id_token):
    """Verifies the token belongs to an Admin or Super Admin."""
    try:
        decoded_token = auth_module.verify_id_token(id_token)
        uid = decoded_token['uid']
        user_doc = db.collection('users').document(uid).get()
        user_data = user_doc.to_dict()
        if user_doc.exists and user_data.get('role') in ['Admin', 'Super Admin']:
            return True, uid, user_data
    except Exception as e:
        logging.error(f"Admin token verification failed: {str(e)}", exc_info=True)
        sentry_sdk.capture_exception(e)
    return False, None, None

def verify_super_admin_token(id_token):
    """Verifies the token belongs to a Super Admin."""
    try:
        decoded_token = auth_module.verify_id_token(id_token)
        uid = decoded_token['uid']
        user_doc = db.collection('users').document(uid).get()
        if user_doc.exists and user_doc.to_dict().get('role') == 'Super Admin':
            return True, uid
    except Exception as e:
        logging.error(f"Super Admin Token verification failed: {str(e)}", exc_info=True)
        sentry_sdk.capture_exception(e)
    return False, None

# --- SUPER ADMIN ROUTES ---

@bp.route('/superadmin/institutions', methods=['GET'])
def get_institutions():
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_super_admin, _ = verify_super_admin_token(token)
    if not is_super_admin:
        return jsonify({'message': 'Unauthorized: Super Admin access required'}), 403
    
    try:
        institutions_ref = db.collection('institutions').order_by("name").stream()
        institutions = [doc.to_dict() for doc in institutions_ref]
        return jsonify(institutions), 200
    except Exception as e:
        return jsonify({'message': str(e)}), 500

@bp.route('/superadmin/institutions', methods=['POST'])
def add_institution():
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_super_admin, _ = verify_super_admin_token(token)
    if not is_super_admin:
        return jsonify({'message': 'Unauthorized: Super Admin access required'}), 403
    
    try:
        content = request.get_json(force=True)
        name = content.get('name')
        center_id = content.get('centerId')
        parent_group = content.get('parentGroup')
        if not all([name, center_id, parent_group]):
            return jsonify({'message': 'Missing name, centerId, or parentGroup'}), 400

        institution_ref = db.collection('institutions').document(center_id)
        if institution_ref.get().exists:
            return jsonify({'message': 'Institution with this centerId already exists'}), 409
        
        institution_ref.set({
            'name': name, 'centerId': center_id, 'parentGroup': parent_group,
            'createdAt': firestore.SERVER_TIMESTAMP
        })
        return jsonify({'status': 'success', 'message': 'Institution added successfully'}), 201
    except Exception as e:
        return jsonify({'message': str(e)}), 500

@bp.route('/superadmin/create-admin', methods=['POST'])
def create_admin_user():
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_super_admin, super_admin_uid = verify_super_admin_token(token)
    if not is_super_admin:
        return jsonify({'message': 'Unauthorized: Super Admin access required'}), 403

    try:
        content = request.get_json(force=True)
        email = content.get('email')
        password = content.get('password')
        name = content.get('name')
        manages_group = content.get('managesGroup')

        new_user = auth_module.create_user(email=email, password=password, display_name=name)

        db.collection('users').document(new_user.uid).set({
            'name': name, 'email': email, 'role': 'Admin', 'status': 'active',
            'managesGroup': manages_group
        })

        return jsonify({'status': 'success', 'message': f'Admin user {email} created successfully.'}), 201
    except auth_module.EmailAlreadyExistsError:
        return jsonify({'message': 'This email address is already in use.'}), 409
    except Exception as e:
        return jsonify({'message': str(e)}), 500
        
# --- ADMIN ROUTES ---

@bp.route('/admin/users', methods=['GET'])
def get_all_users():
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_admin, _, admin_data = verify_admin_token(token)
    if not is_admin:
        return jsonify({'message': 'Unauthorized'}), 403
    try:
        users_query = db.collection("users")
        
        if admin_data.get('role') == 'Admin':
            admin_group = admin_data.get('managesGroup')
            if not admin_group: return jsonify([])
            users_query = users_query.where('parentGroup', '==', admin_group)
        
        users_stream = users_query.stream()
        return jsonify([doc.to_dict() | {"uid": doc.id} for doc in users_stream]), 200
    except Exception as e:
        return jsonify({'message': str(e)}), 500

@bp.route('/admin/update-user-status', methods=['POST'])
def update_user_status():
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_admin, admin_uid, _ = verify_admin_token(token)
    if not is_admin:
        return jsonify({'message': 'Unauthorized'}), 403
    try:
        content = request.get_json(force=True)
        uid = content.get("uid")
        updates = {}
        if "status" in content: updates["status"] = content["status"]
        if "role" in content: updates["role"] = content["role"]
        if "hospital" in content:
            updates["hospital"] = content["hospital"]
            updates["centerId"] = content["hospital"]
        
        if not uid or not updates:
            return jsonify({'message': 'UID and fields to update are required'}), 400

        db.collection("users").document(uid).update(updates)
        return jsonify({'status': 'success', 'message': 'User updated successfully'}), 200
    except Exception as e:
        return jsonify({'message': str(e)}), 500

@bp.route('/admin/machines', methods=['POST'])
def add_machines():
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_admin, _, _ = verify_admin_token(token)
    if not is_admin:
        return jsonify({'message': 'Unauthorized'}), 403
    
    try:
        content = request.get_json(force=True)
        center_id = content.get('centerId')
        machine_names = content.get('machines', [])

        batch = db.batch()
        for name in machine_names:
            if not name.strip(): continue
            machine_id = str(uuid.uuid4())
            machine_ref = db.collection('linacs').document(machine_id)
            batch.set(machine_ref, {
                'machineId': machine_id, 'machineName': name, 'centerId': center_id,
                'createdAt': firestore.SERVER_TIMESTAMP
            })
        batch.commit()
        return jsonify({'status': 'success', 'message': f'{len(machine_names)} machine(s) added.'}), 201
    except Exception as e:
        return jsonify({'message': str(e)}), 500

@bp.route('/admin/machines', methods=['GET'])
def get_machines_for_institution():
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_admin, _, _ = verify_admin_token(token)
    if not is_admin:
        return jsonify({'message': 'Unauthorized'}), 403
    center_id = request.args.get('centerId')
    if not center_id:
        return jsonify({'message': 'centerId query parameter is required'}), 400
    try:
        machines_ref = db.collection('linacs').where('centerId', '==', center_id).stream()
        machines = [doc.to_dict() for doc in machines_ref]
        machines.sort(key=lambda x: x.get('machineName', ''))
        return jsonify(machines), 200
    except Exception as e:
        return jsonify({'message': str(e)}), 500

# ... Add the other admin routes here in a similar fashion ...
# For brevity, I'm including a placeholder. You would move the full functions.
@bp.route('/admin/benchmark-metrics', methods=['GET'])
def get_benchmark_metrics():
    # ... Full benchmark logic from original app.py ...
    return jsonify({"message": "Benchmark data placeholder"}), 200

# ... and so on for all the other admin routes like /admin/delete-user,
# /admin/service-impact-analysis, /admin/correlation-analysis, etc.
