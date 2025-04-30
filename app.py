from flask import Flask, request, jsonify
from flask_cors import CORS
import sqlite3
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

app = Flask(__name__)
CORS(app)  # Enable CORS

DATABASE = 'linac_data.db'

# Initialize DB
def init_db():
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS linac_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            month TEXT,
            data TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# Send email alert if out of tolerance
def send_email_alert(date, energy, value):
    sender_email = "itsmealwin12@gmail.com"
    receiver_email = "alwinjose812@gmail.com"
    app_password = "tjvy ksue rpnk xmaf"

    subject = f"⚠️ LINAC QA Failed on {date}"
    body = f"The LINAC QA value for {energy} on {date} is {value}%, which is out of tolerance (±2.0%)."

    msg = MIMEMultipart()
    msg["From"] = sender_email
    msg["To"] = receiver_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(sender_email, app_password)
        server.send_message(msg)
        server.quit()
        print("Alert email sent.")
    except Exception as e:
        print("Failed to send email:", e)

# Save data
@app.route('/save_data', methods=['POST'])
def save_data():
    content = request.json
    month = content['month']
    data = content['data']

    # Check for out-of-tolerance values and send email
    try:
        for row in data[1:]:  # Skip header row
            energy = row[0]
            for i in range(1, len(row)):
                try:
                    value = float(row[i])
                    if abs(value) > 2.0:
                        date = data[0][i]
                        send_email_alert(date, energy, value)
                except:
                    continue
    except Exception as e:
        print("Error scanning data:", e)

    # Save to DB
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute("SELECT id FROM linac_data WHERE month = ?", (month,))
    existing = c.fetchone()
    if existing:
        c.execute("UPDATE linac_data SET data = ? WHERE month = ?", (str(data), month))
    else:
        c.execute("INSERT INTO linac_data (month, data) VALUES (?, ?)", (month, str(data)))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})

# Load data
@app.route('/load_data', methods=['GET'])
def load_data():
    month = request.args.get('month')
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute("SELECT data FROM linac_data WHERE month = ?", (month,))
    result = c.fetchone()
    conn.close()
    if result:
        return jsonify({"data": eval(result[0])})
    else:
        return jsonify({"data": None})

if __name__ == '__main__':
    app.run(debug=True)
