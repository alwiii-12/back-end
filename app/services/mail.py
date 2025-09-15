import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
import logging
import sentry_sdk

# --- EMAIL CONFIG ---
SENDER_EMAIL = os.environ.get('SENDER_EMAIL', 'itsmealwin12@gmail.com')
APP_PASSWORD = os.environ.get('EMAIL_APP_PASSWORD')

def send_notification_email(recipient_email, subject, body):
    """Sends a standard notification email."""
    if not APP_PASSWORD:
        logging.warning(f"🚫 Cannot send notification to {recipient_email}: APP_PASSWORD not configured.")
        sentry_sdk.capture_message(f"EMAIL_APP_PASSWORD not set. Cannot send notification to {recipient_email}.", level="warning")
        return False
    
    msg = MIMEMultipart()
    msg['From'] = SENDER_EMAIL
    msg['To'] = recipient_email
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))
    
    try:
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        server.login(SENDER_EMAIL, APP_PASSWORD)
        server.send_message(msg)
        server.quit()
        logging.info(f"📧 Notification sent to {recipient_email}")
        return True
    except Exception as e:
        logging.error(f"❌ Email error: {str(e)} for recipient {recipient_email}", exc_info=True)
        sentry_sdk.capture_exception(e)
        return False
