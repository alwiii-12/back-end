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
APP_PASSWORD = 'tjvy ksue rpnk xmaf'  # You should move this to an environment variable in production

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

# === EMAIL FUNCTION ===
def send_email(subject, body):
    msg = MIMEMultipart()
    msg['From'] = SENDER_EMAIL
    msg['To'] = RECEIVER_EMAIL
    msg['Subject'] = subject

    msg.attach(MIMEText(body, 'plain'))

    try:
        with smtplib.SMTP('smtp.gmail.com', 587) as server:
            server.starttls()
            server.login(SENDER_EMAIL, APP_PASSWORD)
            server.send_message(msg)
        print("Email sent successfully.")
    except Exception as e:
        print(f"Email sending failed: {e}")

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

    # Send email alert
    send_email(f'Data Saved for {month}', f'The following data was saved:\n\n{data}')

    return jsonify({'status': 'success'})

@app.route('/data', methods=['GET'])
def get_data():
    month = request.args.get('month')
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT data FROM output_data WHERE month = ?', (month,))
    row = cursor.fetchone()
    conn.close()

    if row:
        return jsonify({'month': month, 'data': row[0]})
    else:
        return jsonify({'error': 'No data found for that month'}), 404

if __name__ == '__main__':
    app.run(debug=True)
