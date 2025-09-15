from flask import Blueprint, jsonify, request
from datetime import datetime, timedelta
from calendar import monthrange
import pytz
import uuid
import numpy as np
import pandas as pd # Added for correlation analysis
from scipy import stats
import sentry_sdk
import logging
import json

# Import custom services and modules
from app.services.firebase import db, auth_module
from app.services.mail import send_notification_email
from firebase_admin import firestore

bp = Blueprint('admin', __name__)

# --- CONSTANTS ---
DATA_TYPES = ["output", "flatness", "inline", "crossline"]
DATA_TYPE_CONFIGS = {
    "output": {"warning": 1.8, "tolerance": 2.0},
    "flatness": {"warning": 0.9, "tolerance": 1.0},
    "inline": {"warning": 0.9, "tolerance": 1.0},
    "crossline": {"warning": 0.9, "tolerance": 1.0}
}
ENERGY_TYPES = ["6X", "10X", "15X", "6X FFF", "10X FFF", "6E", "9E", "12E", "15E", "18E"]


# --- HELPER FUNCTIONS ---

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

def calculate_machine_metrics(machine_id, period_days=90):
    all_numeric_values = {dtype: [] for dtype in DATA_TYPES}
    warnings = 0
    oots = 0
    start_date = datetime.now() - timedelta(days=period_days)
    
    months_ref = db.collection("linac_data").document(machine_id).collection("months").stream()

    for month_doc in months_ref:
        month_id_str = month_doc.id.replace("Month_", "")
        try:
            month_dt = datetime.strptime(month_id_str, '%Y-%m')
            if month_dt.year < start_date.year or (month_dt.year == start_date.year and month_dt.month < start_date.month):
                continue
        except ValueError: continue

        month_data = month_doc.to_dict()
        for data_type, config in DATA_TYPE_CONFIGS.items():
            field_name = f"data_{data_type}"
            if field_name in month_data:
                for row in month_data[field_name]:
                    for value in row.get("values", []):
                        try:
                            val = float(value)
                            all_numeric_values[data_type].append(val)
                            abs_val = abs(val)
                            if abs_val > config["tolerance"]: oots += 1
                            elif abs_val >= config["warning"]: warnings += 1
                        except (ValueError, TypeError): continue
    
    machine_doc = db.collection('linacs').document(machine_id).get()
    machine_data = machine_doc.to_dict() if machine_doc.exists else {}
    machine_name = machine_data.get("machineName", machine_id)
    hospital_name = machine_data.get("centerId", "Unknown")

    results = {
        "machineId": machine_id, "machineName": machine_name, "hospital": hospital_name,
        "warnings": warnings, "oots": oots, "metrics": {}
    }
    for data_type, values in all_numeric_values.items():
        results["metrics"][data_type] = {
            "mean_deviation": np.nanmean(values) if values else 0,
            "std_deviation": np.nanstd(values) if values else 0,
            "data_points": len(values)
        }
    return results

def fetch_data_for_period(machine_id, start_date, end_date):
    """ Fetches all 'output' data for a specific machine within a date range. """
    data_points = {}
    
    months_to_check = set()
    current_date = start_date
    while current_date <= end_date:
        months_to_check.add(current_date.strftime("Month_%Y-%m"))
        current_date += timedelta(days=1)
    
    for month_doc_id in months_to_check:
        month_doc = db.collection("linac_data").document(machine_id).collection("months").document(month_doc_id).get()
        if not month_doc.exists:
            continue
        
        month_data = month_doc.to_dict().get("data_output", [])
        month_str = month_doc_id.replace("Month_", "")
        
        for row in month_data:
            energy = row.get("energy")
            if energy not in data_points:
                data_points[energy] = []
                
            for i, value in enumerate(row.get("values", [])):
                day = i + 1
                try:
                    # Use date objects for comparison to avoid timezone issues
                    current_point_date = datetime.strptime(f"{month_str}-{day}", "%Y-%m-%d").date()
                    if start_date.date() <= current_point_date <= end_date.date():
                        if value not in [None, '']:
                            data_points[energy].append(float(value))
                except (ValueError, TypeError):
                    continue
                    
    return data_points

# --- SUPER ADMIN ROUTES ---

@bp.route('/superadmin/institutions', methods=['GET'])
def get_institutions():
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_super_admin, _ = verify_super_admin_token(token)
    if not is_super_admin: return jsonify({'message': 'Unauthorized'}), 403
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
    if not is_super_admin: return jsonify({'message': 'Unauthorized'}), 403
    try:
        content = request.get_json(force=True)
        name, center_id, parent_group = content.get('name'), content.get('centerId'), content.get('parentGroup')
        if not all([name, center_id, parent_group]): return jsonify({'message': 'Missing fields'}), 400

        institution_ref = db.collection('institutions').document(center_id)
        if institution_ref.get().exists: return jsonify({'message': 'Institution with this ID already exists'}), 409
        
        institution_ref.set({
            'name': name, 'centerId': center_id, 'parentGroup': parent_group,
            'createdAt': firestore.SERVER_TIMESTAMP
        })
        return jsonify({'status': 'success', 'message': 'Institution added'}), 201
    except Exception as e:
        return jsonify({'message': str(e)}), 500

@bp.route('/superadmin/institution/<center_id>', methods=['DELETE'])
def delete_institution(center_id):
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_super_admin, _ = verify_super_admin_token(token)
    if not is_super_admin: return jsonify({'message': 'Unauthorized'}), 403
    try:
        db.collection('institutions').document(center_id).delete()
        return jsonify({'status': 'success', 'message': 'Institution deleted successfully'}), 200
    except Exception as e:
        return jsonify({'message': str(e)}), 500

@bp.route('/superadmin/create-admin', methods=['POST'])
def create_admin_user():
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_super_admin, super_admin_uid = verify_super_admin_token(token)
    if not is_super_admin: return jsonify({'message': 'Unauthorized'}), 403
    try:
        content = request.get_json(force=True)
        email, password, name, manages_group = content.get('email'), content.get('password'), content.get('name'), content.get('managesGroup')
        if not all([email, password, name, manages_group]): return jsonify({'message': 'Missing fields'}), 400

        new_user = auth_module.create_user(email=email, password=password, display_name=name)
        db.collection('users').document(new_user.uid).set({
            'name': name, 'email': email, 'role': 'Admin', 'status': 'active', 'managesGroup': manages_group
        })

        db.collection("audit_logs").add({
            "timestamp": firestore.SERVER_TIMESTAMP, "adminUid": super_admin_uid,
            "action": "superadmin_create_admin", "targetUserUid": new_user.uid,
            "details": {"created_user_email": email, "assigned_group": manages_group}
        })

        return jsonify({'status': 'success', 'message': f'Admin user {email} created'}), 201
    except auth_module.EmailAlreadyExistsError:
        return jsonify({'message': 'Email already in use'}), 409
    except Exception as e:
        if 'new_user' in locals() and new_user.uid:
            auth_module.delete_user(new_user.uid) # Rollback auth user creation
        return jsonify({'message': str(e)}), 500
        
# --- ADMIN ROUTES ---

@bp.route('/admin/users', methods=['GET'])
def get_all_users():
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_admin, _, admin_data = verify_admin_token(token)
    if not is_admin: return jsonify({'message': 'Unauthorized'}), 403
    try:
        users_query = db.collection("users")
        if admin_data.get('role') == 'Admin':
            users_query = users_query.where('parentGroup', '==', admin_data.get('managesGroup'))
        
        users_stream = users_query.stream()
        return jsonify([doc.to_dict() | {"uid": doc.id} for doc in users_stream]), 200
    except Exception as e:
        return jsonify({'message': str(e)}), 500

@bp.route('/admin/update-user-status', methods=['POST'])
def update_user_status():
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_admin, admin_uid, _ = verify_admin_token(token)
    if not is_admin: return jsonify({'message': 'Unauthorized'}), 403
    try:
        content = request.get_json(force=True)
        uid = content.get("uid")
        updates = {}
        if "status" in content: updates["status"] = content["status"]
        if "role" in content: updates["role"] = content["role"]
        if "hospital" in content:
            updates["hospital"] = content["hospital"]
            updates["centerId"] = content["hospital"]
        
        if not uid or not updates: return jsonify({'message': 'Missing fields'}), 400
        
        ref = db.collection("users").document(uid)
        old_data = ref.get().to_dict() or {}
        ref.update(updates)

        changes = {k: {"old": old_data.get(k), "new": v} for k, v in updates.items()}
        db.collection("audit_logs").add({
            "timestamp": firestore.SERVER_TIMESTAMP, "adminUid": admin_uid,
            "action": "user_update", "targetUserUid": uid,
            "changes": changes, "hospital": old_data.get("hospital", "N/A")
        })

        return jsonify({'status': 'success', 'message': 'User updated'}), 200
    except Exception as e:
        return jsonify({'message': str(e)}), 500

@bp.route('/admin/delete-user', methods=['DELETE'])
def delete_user():
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_admin, admin_uid, _ = verify_admin_token(token)
    if not is_admin: return jsonify({'message': 'Unauthorized'}), 403
    try:
        uid_to_delete = request.get_json(force=True).get("uid")
        if not uid_to_delete: return jsonify({'message': 'Missing UID'}), 400

        user_ref = db.collection("users").document(uid_to_delete)
        user_data = user_ref.get().to_dict() or {}
        
        auth_module.delete_user(uid_to_delete)
        user_ref.delete()
        
        db.collection("audit_logs").add({
            "timestamp": firestore.SERVER_TIMESTAMP, "adminUid": admin_uid,
            "action": "user_deletion", "targetUserUid": uid_to_delete, "deletedUserData": user_data
        })
        return jsonify({'status': 'success', 'message': 'User deleted'}), 200
    except Exception as e:
        return jsonify({'message': f"Failed to delete user: {str(e)}"}), 500

@bp.route('/admin/machines', methods=['POST'])
def add_machines():
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_admin, _, _ = verify_admin_token(token)
    if not is_admin: return jsonify({'message': 'Unauthorized'}), 403
    try:
        content = request.get_json(force=True)
        center_id, machine_names = content.get('centerId'), content.get('machines', [])
        batch = db.batch()
        for name in machine_names:
            if not name.strip(): continue
            machine_id = str(uuid.uuid4())
            machine_ref = db.collection('linacs').document(machine_id)
            batch.set(machine_ref, {'machineId': machine_id, 'machineName': name, 'centerId': center_id, 'createdAt': firestore.SERVER_TIMESTAMP})
        batch.commit()
        return jsonify({'status': 'success', 'message': f'{len(machine_names)} machine(s) added.'}), 201
    except Exception as e:
        return jsonify({'message': str(e)}), 500

@bp.route('/admin/machines', methods=['GET'])
def get_machines_for_institution():
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_admin, _, _ = verify_admin_token(token)
    if not is_admin: return jsonify({'message': 'Unauthorized'}), 403
    center_id = request.args.get('centerId')
    if not center_id: return jsonify({'message': 'centerId is required'}), 400
    try:
        machines_ref = db.collection('linacs').where('centerId', '==', center_id).stream()
        machines = [doc.to_dict() for doc in machines_ref]
        machines.sort(key=lambda x: x.get('machineName', ''))
        return jsonify(machines), 200
    except Exception as e:
        return jsonify({'message': str(e)}), 500

@bp.route('/admin/machine/<machine_id>', methods=['PUT'])
def update_machine(machine_id):
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_admin, _, _ = verify_admin_token(token)
    if not is_admin: return jsonify({'message': 'Unauthorized'}), 403
    try:
        new_name = request.get_json(force=True).get('machineName')
        if not new_name: return jsonify({'message': 'New name is required'}), 400
        db.collection('linacs').document(machine_id).update({'machineName': new_name})
        return jsonify({'status': 'success', 'message': 'Machine updated'}), 200
    except Exception as e:
        return jsonify({'message': str(e)}), 500

@bp.route('/admin/machine/<machine_id>', methods=['DELETE'])
def delete_machine(machine_id):
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_admin, _, _ = verify_admin_token(token)
    if not is_admin: return jsonify({'message': 'Unauthorized'}), 403
    try:
        db.collection('linacs').document(machine_id).delete()
        return jsonify({'status': 'success', 'message': 'Machine deleted successfully'}), 200
    except Exception as e:
        return jsonify({'message': str(e)}), 500

@bp.route('/admin/benchmark-metrics', methods=['GET'])
def get_benchmark_metrics():
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_admin, _, admin_data = verify_admin_token(token)
    if not is_admin: return jsonify({'message': 'Unauthorized'}), 403
    try:
        period = int(request.args.get('period', 90))
        hospital_filter = request.args.get('hospitalId')
        
        query = db.collection('linacs')
        if hospital_filter:
             query = query.where('centerId', '==', hospital_filter)
        elif admin_data.get('role') == 'Admin':
            hospitals_ref = db.collection('institutions').where('parentGroup', '==', admin_data.get('managesGroup')).stream()
            hospital_ids = [inst.id for inst in hospitals_ref]
            if not hospital_ids: return jsonify([])
            query = query.where('centerId', 'in', hospital_ids[:30]) # Firestore 'in' query limit is 30

        benchmark_data = [calculate_machine_metrics(m.id, period) for m in query.stream()]
        benchmark_data.sort(key=lambda x: (x['oots'], x['warnings']), reverse=True)
        return jsonify(benchmark_data), 200
    except Exception as e:
        return jsonify({'message': str(e)}), 500
        
@bp.route('/admin/correlation-analysis', methods=['GET'])
def get_correlation_analysis():
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_admin, _, _ = verify_admin_token(token)
    if not is_admin: return jsonify({'message': 'Unauthorized'}), 403
    try:
        machine_id, data_type, energy = request.args.get('machineId'), request.args.get('dataType'), request.args.get('energy')
        if not all([machine_id, data_type, energy]): return jsonify({'error': 'Missing parameters'}), 400

        months_ref = db.collection("linac_data").document(machine_id).collection("months").stream()
        qa_values = []
        for doc in months_ref:
            month_data = doc.to_dict()
            field_name = f"data_{data_type}"
            if field_name in month_data:
                month_id_str = doc.id.replace("Month_", "")
                year, mon = map(int, month_id_str.split("-"))
                for row_data in month_data[field_name]:
                    if row_data.get("energy") == energy:
                        for i, value in enumerate(row_data.get("values", [])):
                            day = i + 1
                            try:
                                if value and day <= monthrange(year, mon)[1]:
                                    qa_values.append({"date": f"{year}-{mon:02d}-{day:02d}", "qa_value": float(value)})
                            except (ValueError, TypeError): continue
        if not qa_values: return jsonify({'error': 'No QA data found.'}), 404

        env_docs = db.collection("linac_data").document(machine_id).collection("daily_env").stream()
        env_values = [{'date': doc.id, **doc.to_dict()} for doc in env_docs]
        if not env_values: return jsonify({'error': 'No environmental data found.'}), 404
        
        qa_df, env_df = pd.DataFrame(qa_values), pd.DataFrame(env_values)
        qa_df['date'], env_df['date'] = pd.to_datetime(qa_df['date']), pd.to_datetime(env_df['date'])
        merged_df = pd.merge(qa_df, env_df, on='date', how='inner').dropna()

        if len(merged_df) < 5: return jsonify({'error': f'Not enough overlapping data points ({len(merged_df)}).'}), 404
        
        results = {}
        for factor in ['temperature_celsius', 'pressure_hpa']:
            if factor in merged_df and len(merged_df[factor].unique()) > 1:
                corr, p_value = stats.pearsonr(merged_df['qa_value'], merged_df[factor])
                results[factor] = {'correlation': corr if not np.isnan(corr) else 0.0, 'p_value': p_value if not np.isnan(p_value) else 1.0}

        return jsonify(results), 200
    except Exception as e:
        return jsonify({'message': str(e)}), 500

@bp.route('/admin/audit-logs', methods=['GET'])
def get_audit_logs():
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_admin, _, _ = verify_admin_token(token)
    if not is_admin: return jsonify({'message': 'Unauthorized'}), 403
    try:
        query = db.collection("audit_logs").order_by("timestamp", direction=firestore.Query.DESCENDING)
        if request.args.get('hospitalId'): query = query.where('hospital', '==', request.args.get('hospitalId'))
        if request.args.get('action'): query = query.where('action', '==', request.args.get('action'))
        if request.args.get('date'):
            start_dt = datetime.strptime(request.args.get('date'), '%Y-%m-%d').replace(tzinfo=pytz.UTC)
            end_dt = start_dt + timedelta(days=1)
            query = query.where('timestamp', '>=', start_dt).where('timestamp', '<', end_dt)
        
        logs = []
        for doc in query.limit(200).stream():
            log_data = doc.to_dict()
            if isinstance(log_data.get('timestamp'), datetime):
                log_data['timestamp'] = log_data['timestamp'].astimezone(pytz.timezone('Asia/Kolkata')).strftime('%Y-%m-%d %H:%M:%S')
            logs.append(log_data)
        return jsonify({"logs": logs}), 200
    except Exception as e:
        return jsonify({'message': str(e)}), 500

@bp.route('/admin/service-impact-analysis', methods=['GET'])
def get_service_impact_analysis():
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_admin, _, _ = verify_admin_token(token)
    if not is_admin: return jsonify({'message': 'Unauthorized'}), 403
    machine_id = request.args.get('machineId')
    if not machine_id: return jsonify({'message': 'machineId is required'}), 400
    try:
        service_events = db.collection('linac_data').document(machine_id).collection('service_events').stream()
        results = []
        for event in service_events:
            service_date = datetime.strptime(event.id, "%Y-%m-%d")
            before_data = fetch_data_for_period(machine_id, service_date - timedelta(14), service_date - timedelta(1))
            after_data = fetch_data_for_period(machine_id, service_date + timedelta(1), service_date + timedelta(14))
            
            all_energies = set(before_data.keys()) | set(after_data.keys())
            for energy in all_energies:
                before_vals = before_data.get(energy, [])
                after_vals = after_data.get(energy, [])
                if not before_vals or not after_vals: continue

                before_metrics = {"mean_deviation": np.mean(before_vals), "std_deviation": np.std(before_vals)}
                after_metrics = {"mean_deviation": np.mean(after_vals), "std_deviation": np.std(after_vals)}
                
                improvement = 0
                if before_metrics["std_deviation"] > 0:
                    improvement = ((before_metrics["std_deviation"] - after_metrics["std_deviation"]) / before_metrics["std_deviation"]) * 100

                results.append({
                    "service_date": event.id, "energy": energy,
                    "before_metrics": before_metrics, "after_metrics": after_metrics,
                    "stability_improvement_percent": improvement,
                    "before_data": before_vals, "after_data": after_vals
                })
        results.sort(key=lambda x: x['service_date'], reverse=True)
        return jsonify(results), 200
    except Exception as e:
        return jsonify({'message': str(e)}), 500

@bp.route('/admin/hospital-data', methods=['GET'])
def get_hospital_data():
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_admin, _, _ = verify_admin_token(token)
    if not is_admin: return jsonify({'message': 'Unauthorized'}), 403
    try:
        machine_id = request.args.get('machineId')
        month_param = request.args.get('month')
        if not machine_id or not month_param:
            return jsonify({'error': 'Missing machineId or month'}), 400

        year, mon = map(int, month_param.split("-"))
        _, num_days = monthrange(year, mon)
        all_data = {}
        for data_type in DATA_TYPES:
            doc_ref = db.collection("linac_data").document(machine_id).collection("months").document(f"Month_{month_param}")
            doc = doc_ref.get()
            
            energy_dict = {e: [""] * num_days for e in ENERGY_TYPES}
            if doc.exists:
                doc_data = doc.to_dict().get(f"data_{data_type}", [])
                for row in doc_data:
                    energy, values = row.get("energy"), row.get("values", [])
                    if energy in energy_dict:
                        energy_dict[energy] = (values + [""] * num_days)[:num_days]
            table = [[e] + energy_dict[e] for e in ENERGY_TYPES]
            all_data[data_type] = table
        return jsonify({'data': all_data}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500
