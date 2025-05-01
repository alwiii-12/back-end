from flask import Flask, request, jsonify
from flask_cors import CORS
import sqlite3
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

app = Flask(__name__)
CORS(app)

# === CONFIGURATION ===
DB_NAME = 'linac_data.db'
SENDER_EMAIL = 'itsmealwin12@gmail.com'
RECEIVER_EMAIL = 'alwinjose812@gmail.com'
APP_PASSWORD = 'tjvy ksue rpnk xmaf'

# === SETUP DATABASE ===
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS output_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            month TEXT NOT NULL,
            data TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# === ROUTES ===

@app.route('/save', methods=['POST'])
def save_data():
    content = request.get_json()
    month = content['month']
    data = str(content['data'])

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM output_data WHERE month = ?', (month,))
    cursor.execute('INSERT INTO output_data (month, data) VALUES (?, ?)', (month, data))
    conn.commit()
    conn.close()
    return jsonify({'status': 'success'})

@app.route('/data', methods=['GET'])
def get_data():
    month = request.args.get('month')
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT data FROM output_data WHERE month = ?', (month,))
    row = cursor.fetchone()
    conn.close()
    return jsonify({'data': eval(row[0]) if row else []})

@app.route('/send-alert', methods=['POST'])
def send_alert():
    content = request.get_json()
    out_values = content.get('outValues', [])
    if not out_values:
        return jsonify({'status': 'no alerts sent'})

    message_body = "The following LINAC QA output values are out of tolerance (±2.0%):\n\n"
    for val in out_values:
        message_body += f"Energy: {val['energy']}, Date: {val['date']}, Value: {val['value']}%\n"

    msg = MIMEMultipart()
    msg['From'] = SENDER_EMAIL
    msg['To'] = RECEIVER_EMAIL
    msg['Subject'] = '⚠ LINAC QA Output Failed Alert'

    msg.attach(MIMEText(message_body, 'plain'))

    try:
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        server.login(SENDER_EMAIL, APP_PASSWORD)
        server.send_message(msg)
        server.quit()
        return jsonify({'status': 'alert sent'})
    except Exception as e:
        print("Email sending error:", e)
        return jsonify({'status': 'error', 'message': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)
