import firebase_admin
from firebase_admin import credentials, firestore
import pandas as pd
from datetime import datetime, timedelta
from calendar import monthrange
import os
import json
from io import BytesIO

# Imports for sending email with attachments
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

# --- INITIALIZE FIREBASE ADMIN ---
try:
    if 'FIREBASE_CREDENTIALS' in os.environ:
        creds_json = json.loads(os.environ.get('FIREBASE_CREDENTIALS'))
        cred = credentials.Certificate(creds_json)
        print("Firebase credentials loaded from environment variable.")
    else:
        # Fallback for local testing if you have the file
        cred = credentials.Certificate("firebase_credentials.json")
        print("Firebase credentials file found locally.")
except Exception as e:
    print(f"Could not initialize Firebase: {e}")
    raise Exception("CRITICAL: Ensure Firebase credentials are set up correctly.")

if not firebase_admin._apps:
    firebase_admin.initialize_app(cred)
    print("Firebase default app initialized for weekly summary service.")

db = firestore.client()

# --- EMAIL CONFIG ---
SENDER_EMAIL = os.environ.get('SENDER_EMAIL', 'itsmealwin12@gmail.com')
APP_PASSWORD = os.environ.get('EMAIL_APP_PASSWORD')
DATA_TYPES = ["output", "flatness", "inline", "crossline"]
ENERGY_TYPES = ["6X", "10X", "15X", "6X FFF", "10X FFF", "6E", "9E", "12E", "15E", "18E"]

def send_summary_email(recipient_email, subject, body, attachment_data, filename):
    """Sends an email with an Excel file attachment."""
    if not APP_PASSWORD:
        print(f"🚫 Cannot send summary to {recipient_email}: EMAIL_APP_PASSWORD not configured.")
        return False
    
    msg = MIMEMultipart()
    msg['From'] = SENDER_EMAIL
    msg['To'] = recipient_email
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))
    
    # Attach the Excel file
    part = MIMEApplication(attachment_data, Name=filename)
    part['Content-Disposition'] = f'attachment; filename="{filename}"'
    msg.attach(part)
    
    try:
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        server.login(SENDER_EMAIL, APP_PASSWORD)
        server.send_message(msg)
        server.quit()
        print(f"📧 Weekly summary sent successfully to {recipient_email}")
        return True
    except Exception as e:
        print(f"❌ Email error for weekly summary: {str(e)} for recipient {recipient_email}")
        return False

def fetch_data_for_period(machine_id, start_date, end_date):
    """Fetches all QA data types for a machine within a date range."""
    all_data = {dtype: [] for dtype in DATA_TYPES}
    
    # Determine which month documents we need to check
    months_to_check = set()
    current_date = start_date
    while current_date <= end_date:
        months_to_check.add(current_date.strftime("Month_%Y-%m"))
        # Move to the first day of the next month to ensure we get all unique months
        next_month_year = current_date.year if current_date.month < 12 else current_date.year + 1
        next_month = current_date.month + 1 if current_date.month < 12 else 1
        current_date = datetime(next_month_year, next_month, 1)

    for month_doc_id in months_to_check:
        month_doc = db.collection("linac_data").document(machine_id).collection("months").document(month_doc_id).get()
        if not month_doc.exists:
            continue
        
        month_data = month_doc.to_dict()
        month_str = month_doc_id.replace("Month_", "")
        
        for data_type in DATA_TYPES:
            field_name = f"data_{data_type}"
            if field_name in month_data:
                for row in month_data[field_name]:
                    energy = row.get("energy")
                    for i, value in enumerate(row.get("values", [])):
                        day = i + 1
                        try:
                            current_point_date = datetime.strptime(f"{month_str}-{day}", "%Y-%m-%d").date()
                            if start_date <= current_point_date <= end_date:
                                if value not in [None, '']:
                                    all_data[data_type].append({
                                        "Date": current_point_date.strftime("%Y-%m-%d"),
                                        "Energy": energy,
                                        "Value (%)": float(value)
                                    })
                        except (ValueError, TypeError):
                            continue
    return all_data

if __name__ == '__main__':
    print("--- 🚀 Starting Weekly Summary Service ---")
    
    # 1. Get RSO emails for each center
    rso_map = {}
    users_ref = db.collection('users').where('role', '==', 'RSO').stream()
    for user in users_ref:
        user_data = user.to_dict()
        center_id = user_data.get('centerId')
        email = user_data.get('email')
        if center_id and email:
            if center_id not in rso_map:
                rso_map[center_id] = []
            rso_map[center_id].append(email)

    # 2. Get all machines grouped by center
    machines_by_center = {}
    linacs_ref = db.collection('linacs').stream()
    for linac in linacs_ref:
        linac_data = linac.to_dict()
        center_id = linac_data.get('centerId')
        if center_id:
            if center_id not in machines_by_center:
                machines_by_center[center_id] = []
            machines_by_center[center_id].append(linac_data)

    # 3. Define date range for the last week
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=7)
    date_range_str = f"{start_date.strftime('%d-%b-%Y')} to {end_date.strftime('%d-%b-%Y')}"
    print(f"Processing data for period: {date_range_str}")

    # 4. Loop through each center, generate and send the report
    for center_id, machines in machines_by_center.items():
        print(f"\nProcessing Center: {center_id}...")
        
        rso_emails = rso_map.get(center_id)
        if not rso_emails:
            print(f"⚠️ No RSO found for center {center_id}. Skipping.")
            continue
        
        output_buffer = BytesIO()
        with pd.ExcelWriter(output_buffer, engine='openpyxl') as writer:
            has_any_data = False
            for data_type in DATA_TYPES:
                weekly_data_for_type = []
                for machine in machines:
                    machine_id = machine.get('machineId')
                    machine_name = machine.get('machineName', 'Unknown')
                    
                    data_points = fetch_data_for_period(machine_id, start_date, end_date).get(data_type, [])
                    for point in data_points:
                        point['Machine'] = machine_name
                        weekly_data_for_type.append(point)
                
                if weekly_data_for_type:
                    has_any_data = True
                    df = pd.DataFrame(weekly_data_for_type)
                    # Reorder columns for clarity
                    df = df[['Date', 'Machine', 'Energy', 'Value (%)']]
                    df.to_excel(writer, sheet_name=data_type.title(), index=False)
                    print(f"  - Added {len(df)} rows to '{data_type.title()}' sheet.")

        if has_any_data:
            subject = f"LINAC QA Weekly Summary: {center_id}"
            body = (f"Hello,\n\nPlease find the attached weekly summary of LINAC QA data for {center_id}.\n"
                    f"This report covers the period from {date_range_str}.\n\n"
                    "Regards,\nLINAC QA Portal")
            
            excel_filename = f"Weekly_QA_Summary_{center_id}_{end_date.strftime('%Y-%m-%d')}.xlsx"
            
            # Send email to all RSOs of that center
            send_summary_email(", ".join(rso_emails), subject, body, output_buffer.getvalue(), excel_filename)
        else:
            print(f"  - No data found for any machine in {center_id} for the last 7 days. No report sent.")

    print("\n--- ✅ Weekly Summary Service Finished ---")
