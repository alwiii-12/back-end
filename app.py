t("centerId", ""),
            'status': user_data.get("status", "unknown")
        }), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': 'Login failed'}), 500

# --- SAVE DATA ---
@app.route('/save', methods=['POST'])
def save_data():
    try:
        content = request.get_json(force=True)
        uid = content.get("uid")
        month = f"Month_{content.get('month')}"
        raw_data = content.get("data")

        user_doc = db.collection('users').document(uid).get()
        if not user_doc.exists:
            return jsonify({'status': 'error', 'message': 'User not found'}), 404
        user_data = user_doc.to_dict()
        center_id = user_data.get("centerId")
        user_status = user_data.get("status", "pending")

        if user_status != "active":
            return jsonify({'status': 'error', 'message': 'Account not active'}), 403
        if not center_id:
            return jsonify({'status': 'error', 'message': 'Missing centerId'}), 400
        if not isinstance(raw_data, list):
            return jsonify({'status': 'error', 'message': 'Invalid data'}), 400

        converted = [{"row": i, "energy": row[0], "values": row[1:]} for i, row in enumerate(raw_data) if len(row) > 1]
        db.collection("linac_data").document(center_id).collection("months").document(month).set(
            {"data": converted}, merge=True)
        return jsonify({'status': 'success'}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

# --- LOAD DATA ---
@app.route('/data', methods=['GET'])
def get_data():
    try:
        month_param = request.args.get('month')
        uid = request.args.get('uid')
        if not month_param or not uid:
            return jsonify({'error': 'Missing "month" or "uid"'}), 400

        user_doc = db.collection("users").document(uid).get()
        if not user_doc.exists:
            return jsonify({'error': 'User not found'}), 404
        user_data = user_doc.to_dict()
        center_id = user_data.get("centerId")
        user_status = user_data.get("status", "pending")

        if user_status != "active":
            return jsonify({'error': 'Account not active'}), 403
        if not center_id:
            return jsonify({'error': 'Missing centerId'}), 400

        year, mon = map(int, month_param.split("-"))
        _, num_days = monthrange(year, mon)
        energy_dict = {e: [""] * num_days for e in ENERGY_TYPES}

        doc = db.collection("linac_data").document(center_id).collection("months").document(f"Month_{month_param}").get()
        if doc.exists:
            for row in doc.to_dict().get("data", []):
                energy, values = row.get("energy"), row.get("values", [])
                if energy in energy_dict:
                    energy_dict[energy] = values

        table = [[e] + energy_dict[e] for e in ENERGY_TYPES]
        return jsonify({'data': table}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# --- ALERT EMAIL ---
@app.route('/send-alert', methods=['POST'])
def send_alert():
    try:
        content = request.get_json(force=True)
        out_values = content.get("outValues", [])
        hospital = content.get("hospitalName", "Unknown")
        if not out_values:
            return jsonify({'status': 'no alerts sent'})
        message = f"Alert from {hospital}\n\nOut-of-tolerance values (±2.0%):\n\n"
        for v in out_values:
            message += f"Energy: {v['energy']}, Date: {v['date']}, Value: {v['value']}%\n"

        msg = MIMEMultipart()
        msg['From'] = SENDER_EMAIL
        msg['To'] = RECEIVER_EMAIL
        msg['Subject'] = f"⚠ LINAC QA Alert - {hospital}"
        msg.attach(MIMEText(message, 'plain'))

        if APP_PASSWORD:
            server = smtplib.SMTP_SSL("smtp.gmail.com", 465)
            server.login(SENDER_EMAIL, APP_PASSWORD)
            server.send_message(msg)
            server.quit()
            return jsonify({'status': 'alert sent'}), 200
        else:
            return jsonify({'status': 'email not sent'}), 500
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

# --- ADMIN: GET PENDING USERS ---
@app.route('/admin/pending-users', methods=['GET'])
async def get_pending_users():
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_admin, _ = await verify_admin_token(token)
    if not is_admin:
        return jsonify({'message': 'Unauthorized'}), 403
    try:
        users = db.collection("users").where("status", "==", "pending").stream()
        return jsonify([doc.to_dict() | {"uid": doc.id} for doc in users]), 200
    except Exception as e:
        return jsonify({'message': str(e)}), 500

# --- ADMIN: UPDATE USER STATUS ---
@app.route('/admin/update-user-status', methods=['POST'])
async def update_user_status():
    token = request.headers.get("Authorization", "").split("Bearer ")[-1]
    is_admin, admin_uid = await verify_admin_token(token)
    if not is_admin:
        return jsonify({'message': 'Unauthorized'}), 403
    try:
        content = request.get_json(force=True)
        uid = content.get("uid")
        status = content.get("status")
        if not uid or status not in ["active", "rejected"]:
            return jsonify({'message': 'Invalid input'}), 400
        ref = db.collection("users").document(uid)
        ref.update({"status": status})
        data = ref.get().to_dict()
        if APP_PASSWORD and data.get("email"):
            msg = "Your LINAC QA account has been " + ("approved." if status == "active" else "rejected.")
            send_notification_email(data["email"], "LINAC QA Status Update", msg)
        return jsonify({'status': 'success'}), 200
    except Exception as e:
        return jsonify({'message': str(e)}), 500

# --- INDEX ---
@app.route('/')
def index():
    return "✅ LINAC QA Backend Running"

# --- RUN ---
if __name__ == '__main__':
    app.run(debug=True)
