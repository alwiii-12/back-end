from flask import Blueprint, jsonify, request, send_file
import logging
import json
from calendar import monthrange
from datetime import datetime
import pandas as pd
from io import BytesIO
from prophet import Prophet
import sentry_sdk

# Import custom services and modules
from app.services.firebase import db, auth_module
from app.services.mail import send_notification_email
from firebase_admin import firestore

bp = Blueprint('data', __name__)

# --- CONSTANTS ---
ENERGY_TYPES = ["6X", "10X", "15X", "6X FFF", "10X FFF", "6E", "9E", "12E", "15E", "18E"]
DATA_TYPES = ["output", "flatness", "inline", "crossline"]
DATA_TYPE_CONFIGS = {
    "output": {"warning": 1.8, "tolerance": 2.0},
    "flatness": {"warning": 0.9, "tolerance": 1.0},
    "inline": {"warning": 0.9, "tolerance": 1.0},
    "crossline": {"warning": 0.9, "tolerance": 1.0}
}

# --- HELPER FUNCTIONS ---
def find_new_warnings(old_data, new_data, config):
    old_warnings = set()
    for row in old_data:
        energy = row.get("energy")
        for i, value in enumerate(row.get("values", [])):
            try:
                val = abs(float(value))
                if val >= config["warning"] and val <= config["tolerance"]:
                    old_warnings.add(f"{energy}-{i}")
            except (ValueError, TypeError):
                continue
    
    new_warnings = []
    for row in new_data:
        energy = row[0]
        for i, value in enumerate(row[1:]):
            try:
                val = abs(float(value))
                if val >= config["warning"] and val <= config["tolerance"]:
                    if f"{energy}-{i}" not in old_warnings:
                        new_warnings.append({"energy": energy, "value": val})
            except (ValueError, TypeError):
                continue
    return new_warnings

def verify_user_token(id_token):
    """Verifies a generic user token and returns their UID and user data."""
    try:
        decoded_token = auth_module.verify_id_token(id_token)
        uid = decoded_token['uid']
        user_doc = db.collection('users').document(uid).get()
        if user_doc.exists:
            return True, uid, user_doc.to_dict()
    except Exception as e:
        logging.error(f"User token verification failed: {str(e)}", exc_info=True)
        sentry_sdk.capture_exception(e)
    return False, None, None
    
# --- ROUTES ---

@bp.route('/save-annotation', methods=['POST'])
def save_annotation():
    try:
        content = request.get_json(force=True)
        machine_id = content.get("machineId")
        month = content.get("month")
        key = content.get("key")
        data = content.get("data")

        if not all([machine_id, month, key, data]):
            return jsonify({'status': 'error', 'message': 'Missing required fields'}), 400

        annotation_ref = db.collection('linac_data').document(machine_id).collection('annotations').document(month).collection('keys').document(key)
        annotation_ref.set(data)

        is_service_event = data.get('isServiceEvent', False)
        event_date = data.get('eventDate')
        
        if event_date:
            service_event_ref = db.collection('linac_data').document(machine_id).collection('service_events').document(event_date)
            if is_service_event:
                service_event_ref.set({
                    'description': data.get('text', 'Service/Calibration'),
                    'energy': data.get('energy'),
                    'dataType': data.get('dataType')
                })
            else:
                service_event_ref.delete()

        return jsonify({'status': 'success', 'message': 'Annotation saved successfully'}), 200
    except Exception as e:
        logging.error(f"Save annotation failed: {str(e)}", exc_info=True)
        sentry_sdk.capture_exception(e)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@bp.route('/delete-annotation', methods=['POST'])
def delete_annotation():
    try:
        content = request.get_json(force=True)
        machine_id = content.get("machineId")
        month = content.get("month")
        key = content.get("key")

        if not all([machine_id, month, key]):
            return jsonify({'status': 'error', 'message': 'Missing required fields'}), 400
        
        annotation_ref = db.collection('linac_data').document(machine_id).collection('annotations').document(month).collection('keys').document(key)
        annotation_ref.delete()

        event_date = key.split('-', 1)[1]
        if event_date:
            service_event_ref = db.collection('linac_data').document(machine_id).collection('service_events').document(event_date)
            service_event_ref.delete()

        return jsonify({'status': 'success', 'message': 'Annotation deleted successfully'}), 200
    except Exception as e:
        logging.error(f"Delete annotation failed: {str(e)}", exc_info=True)
        sentry_sdk.capture_exception(e)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@bp.route('/save', methods=['POST'])
def save_data():
    try:
        content = request.get_json(force=True)
        uid = content.get("uid")
        month_param = content.get("month")
        raw_data = content.get("data")
        data_type = content.get("dataType")
        machine_id = content.get("machineId") 

        if not data_type or data_type not in DATA_TYPES:
            return jsonify({'status': 'error', 'message': 'Invalid or missing dataType'}), 400
        if not machine_id:
            return jsonify({'status': 'error', 'message': 'machineId is required'}), 400

        month_doc_id = f"Month_{month_param}"
        firestore_field_name = f"data_{data_type}"
        doc_ref = db.collection("linac_data").document(machine_id).collection("months").document(month_doc_id)
        
        old_data_doc = doc_ref.get()
        old_data = old_data_doc.to_dict().get(firestore_field_name, []) if old_data_doc.exists else []
        
        new_warnings = find_new_warnings(old_data, raw_data, DATA_TYPE_CONFIGS[data_type])
        
        if new_warnings:
            first_warning = new_warnings[0]
            topic = "output_drift" if data_type == "output" else "flatness_warning"
            db.collection("proactive_chats").add({
                "uid": uid, "read": False, "timestamp": firestore.SERVER_TIMESTAMP,
                "initial_message": f"I noticed a new warning for {first_warning['energy']} ({data_type.title()}). The value was {first_warning['value']}%. Would you like help diagnosing this?",
                "topic": topic
            })

        converted = [{"row": i, "energy": row[0], "values": row[1:]} for i, row in enumerate(raw_data) if len(row) > 1]
        doc_ref.set({firestore_field_name: converted}, merge=True)
        
        return jsonify({'status': 'success', 'message': f'{data_type} data saved successfully'}), 200
    except Exception as e:
        logging.error(f"Save data failed for {data_type}: {str(e)}", exc_info=True)
        sentry_sdk.capture_exception(e)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@bp.route('/save-daily-env', methods=['POST'])
def save_daily_env():
    try:
        content = request.get_json(force=True)
        machine_id = content.get("machineId")
        date = content.get("date")
        temperature = content.get("temperature")
        pressure = content.get("pressure")

        if not all([machine_id, date]):
            return jsonify({'status': 'error', 'message': 'Missing machineId or date'}), 400
        
        update_data = {}
        if temperature is not None:
            try: update_data['temperature_celsius'] = float(temperature)
            except (ValueError, TypeError): pass
        if pressure is not None:
            try: update_data['pressure_hpa'] = float(pressure)
            except (ValueError, TypeError): pass

        if not update_data:
             return jsonify({'status': 'no_change', 'message': 'No valid data to save'}), 200

        doc_ref = db.collection("linac_data").document(machine_id).collection("daily_env").document(date)
        doc_ref.set(update_data, merge=True)

        return jsonify({'status': 'success', 'message': f'Environmental data for {date} saved'}), 200
    except Exception as e:
        logging.error(f"Save daily env data failed: {str(e)}", exc_info=True)
        sentry_sdk.capture_exception(e)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@bp.route('/data', methods=['GET'])
def get_data():
    try:
        month_param = request.args.get('month')
        machine_id = request.args.get('machineId')
        data_type = request.args.get('dataType')

        if not all([month_param, machine_id, data_type]):
            return jsonify({'error': 'Missing required parameters'}), 400

        year, mon = map(int, month_param.split("-"))
        _, num_days = monthrange(year, mon)
        energy_dict = {e: [""] * num_days for e in ENERGY_TYPES}
        
        firestore_field_name = f"data_{data_type}"
        doc = db.collection("linac_data").document(machine_id).collection("months").document(f"Month_{month_param}").get()

        if doc.exists:
            doc_data = doc.to_dict().get(firestore_field_name, [])
            for row in doc_data:
                energy, values = row.get("energy"), row.get("values", [])
                if energy in energy_dict:
                    energy_dict[energy] = (values + [""] * num_days)[:num_days]

        table = [[e] + energy_dict[e] for e in ENERGY_TYPES]
        
        env_data = {}
        env_docs = db.collection("linac_data").document(machine_id).collection("daily_env").stream()
        for doc in env_docs:
            if doc.id.startswith(month_param):
                env_data[doc.id] = doc.to_dict()

        return jsonify({'data': table, 'env_data': env_data}), 200
    except Exception as e:
        logging.error(f"Get data failed for {data_type}: {str(e)}", exc_info=True)
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500

@bp.route('/export-excel', methods=['POST'])
def export_excel():
    try:
        content = request.get_json(force=True)
        month_param = content.get('month')
        data_type = content.get('dataType')
        machine_id = content.get('machineId')

        if not all([month_param, data_type, machine_id]):
            return jsonify({'error': 'Missing required parameters'}), 400

        doc_ref = db.collection("linac_data").document(machine_id).collection("months").document(f"Month_{month_param}")
        doc = doc_ref.get()

        if not doc.exists:
            return jsonify({'error': 'No data found for the selected machine and month'}), 404

        firestore_field_name = f"data_{data_type}"
        doc_data = doc.to_dict().get(firestore_field_name, [])
        if not doc_data:
            return jsonify({'error': f'No {data_type} data found for the selected period'}), 404

        year, mon = map(int, month_param.split("-"))
        _, num_days = monthrange(year, mon)
        data_for_df = []
        for row in doc_data:
            energy, values = row.get("energy"), row.get("values", [])
            padded_values = (values + [""] * num_days)[:num_days]
            data_for_df.append([energy] + padded_values)

        df = pd.DataFrame(data_for_df, columns=["Energy"] + list(range(1, num_days + 1)))
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name=f'{data_type.title()} Data')
        output.seek(0)
        
        return send_file(output, download_name=f'LINAC_QA_{data_type.upper()}_{month_param}.xlsx', mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True)
    except Exception as e:
        logging.error(f"Excel export failed for {data_type}: {str(e)}", exc_info=True)
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500

@bp.route('/send-alert', methods=['POST'])
def send_alert():
    try:
        content = request.get_json(force=True)
        uid = content.get("uid")
        machine_id = content.get("machineId")

        if not all([uid, machine_id]):
            return jsonify({'status': 'error', 'message': 'Missing uid or machineId'}), 400

        user_doc = db.collection('users').document(uid).get()
        if not user_doc.exists: return jsonify({'status': 'error', 'message': 'User not found'}), 404
        user_data = user_doc.to_dict()
        center_id = user_data.get('centerId') 
        if not center_id: return jsonify({'status': 'error', 'message': 'Center ID not found for user'}), 400

        rso_users_stream = db.collection('users').where('centerId', '==', center_id).where('role', '==', 'RSO').stream()
        recipient_emails = [rso.to_dict()['email'] for rso in rso_users_stream if 'email' in rso.to_dict()]
        
        if not recipient_emails:
            return jsonify({'status': 'no_rso_email', 'message': f'No RSO email found for hospital {center_id}.'}), 200
        
        content_data = {
            "outValues": content.get("outValues", []), "hospitalName": content.get("hospitalName", "Unknown"),
            "month": content.get("month"), "dataType": content.get("dataType", "output"), "tolerance": content.get("tolerance", 2.0)
        }
        
        machine_doc = db.collection('linacs').document(machine_id).get()
        machine_name = machine_doc.to_dict().get('machineName', machine_id) if machine_doc.exists else machine_id

        alerts_doc_ref = db.collection("linac_alerts").document(machine_id).collection("months").document(f"Month_{content_data['month']}_{content_data['dataType']}")
        alerts_doc_snap = alerts_doc_ref.get()
        
        previously_alerted_strings = set(json.dumps(val, sort_keys=True) for val in (alerts_doc_snap.to_dict().get("alerted_values", []) if alerts_doc_snap.exists else []))
        current_out_values_strings = set(json.dumps(val, sort_keys=True) for val in content_data['outValues'])

        if current_out_values_strings == previously_alerted_strings:
            return jsonify({'status': 'no_change', 'message': 'No new alerts or changes. Email not sent.'})

        subject = f"⚠ {content_data['dataType'].title()} QA Status - {content_data['hospitalName']} ({machine_name}) - {content_data['month']}"
        message_body = f"Message body content..." # Simplified for brevity
        
        email_sent = send_notification_email(", ".join(recipient_emails), subject, message_body)

        if email_sent:
            alerts_doc_ref.set({"alerted_values": content_data['outValues']}, merge=False)
            return jsonify({'status': 'alert sent'}), 200
        else:
            return jsonify({'status': 'email_send_error', 'message': 'Failed to send email.'}), 500

    except Exception as e:
        sentry_sdk.capture_exception(e)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@bp.route('/predictions', methods=['GET'])
def get_predictions():
    try:
        machine_id = request.args.get('machineId')
        data_type = request.args.get('dataType')
        energy = request.args.get('energy')
        month = request.args.get('month')

        if not all([data_type, energy, month, machine_id]):
            return jsonify({'error': 'Missing required parameters'}), 400

        prediction_doc_id = f"{machine_id}_{data_type}_{energy}_{month}"
        prediction_doc = db.collection("linac_predictions").document(prediction_doc_id).get()

        if prediction_doc.exists:
            return jsonify(prediction_doc.to_dict()), 200
        else:
            return jsonify({'error': f'Prediction not found for {month}'}), 404
            
    except Exception as e:
        logging.error(f"Get predictions failed: {str(e)}", exc_info=True)
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500

@bp.route('/historical-forecast', methods=['POST'])
def get_historical_forecast():
    try:
        content = request.get_json(force=True)
        month = content.get('month')
        data_type = content.get('dataType')
        energy = content.get('energy')
        machine_id = content.get('machineId')

        if not all([month, data_type, energy, machine_id]):
            return jsonify({'error': 'Missing required parameters'}), 400
        
        months_ref = db.collection("linac_data").document(machine_id).collection("months").stream()
        all_vals = []
        for month_doc in months_ref:
            if month_doc.id.replace("Month_", "") >= month: continue
            month_data = month_doc.to_dict().get(f"data_{data_type}", [])
            for row_data in month_data:
                if row_data.get("energy") == energy:
                    # ... logic to extract values ...
                    pass # Simplified for brevity

        historical_df = pd.DataFrame(all_vals)
        if historical_df.empty or len(historical_df) < 10:
            return jsonify({'error': 'Not enough historical data.'}), 400

        model = Prophet()
        model.fit(historical_df)
        # ... rest of forecast logic ...

        return jsonify({'forecast': json.loads(forecast.to_json(orient='records')), 'actuals': actuals})
    except Exception as e:
        logging.error(f"Error in historical forecast: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500

@bp.route('/query-qa-data', methods=['POST'])
def query_qa_data():
    try:
        content = request.get_json(force=True)
        user_query_text = content.get("query_text", "").lower()
        with open('knowledge_base.json', 'r') as f: kb = json.load(f)

        if 'drift' in user_query_text or 'output' in user_query_text: topic = 'output_drift'
        elif 'flatness' in user_query_text or 'symmetry' in user_query_text: topic = 'flatness_warning'
        else: return jsonify({'status': 'error', 'message': "I can help diagnose 'output drift' or 'flatness'."}), 404

        flow = kb.get("troubleshooting", {}).get(topic)
        start_node = flow.get('nodes', {}).get(flow.get('start_node'))
        
        return jsonify({
            'status': 'diagnostic_start', 'topic': topic,
            'node_id': flow.get('start_node'), 'question': start_node.get('question'),
            'options': start_node.get('options', [])
        }), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@bp.route('/diagnose-step', methods=['POST'])
def diagnose_step():
    try:
        content = request.get_json(force=True)
        with open('knowledge_base.json', 'r') as f: kb = json.load(f)
        flow = kb.get("troubleshooting", {}).get(content.get("topic"))
        current_node = flow.get("nodes", {}).get(content.get("current_node_id"))
        next_node_id = current_node.get("answers", {}).get(content.get("answer"))
        next_node = flow.get("nodes", {}).get(next_node_id)

        if "diagnosis" in next_node:
            return jsonify({'status': 'diagnostic_end', 'diagnosis': next_node.get('diagnosis')}), 200
        elif "question" in next_node:
            return jsonify({
                'status': 'diagnostic_continue', 'topic': content.get("topic"), 'node_id': next_node_id,
                'question': next_node.get('question'), 'options': next_node.get('options', [])
            }), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@bp.route('/user/machines', methods=['GET'])
def get_user_machines():
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_valid, _, user_data = verify_user_token(token)
    if not is_valid or not user_data: return jsonify({'message': 'Unauthorized'}), 403
    center_id = user_data.get('centerId')
    if not center_id: return jsonify({'message': 'User is not associated with an institution.'}), 400
    try:
        machines_ref = db.collection('linacs').where('centerId', '==', center_id).stream()
        machines = [doc.to_dict() for doc in machines_ref]
        machines.sort(key=lambda x: x.get('machineName', ''))
        return jsonify(machines), 200
    except Exception as e:
        return jsonify({'message': str(e)}), 500

# Placeholder for update-live-forecast
@bp.route('/update-live-forecast', methods=['POST'])
def update_live_forecast():
    return jsonify({'status': 'error', 'message': 'Endpoint not yet updated for multi-machine support.'}), 501
