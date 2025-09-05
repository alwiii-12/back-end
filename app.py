import os
import json
import logging
from flask import Flask, request, jsonify
from flask_cors import CORS
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import jwt

import firebase_admin
from firebase_admin import credentials, firestore, auth, app_check
import sentry_sdk
from sentry_sdk.integrations.flask import FlaskIntegration

# --- BLUEPRINT IMPORTS ---
from blueprints.auth import auth_bp
from blueprints.data import data_bp
from blueprints.forecasting import forecasting_bp
from blueprints.admin import admin_bp

# ==============================================================================
# --- APPLICATION FACTORY FUNCTION ---
# ==============================================================================

def create_app():
    """Creates and configures the Flask application."""
    app = Flask(__name__)
    app.logger.setLevel(logging.DEBUG)

    # --- Sentry Initialization ---
    SENTRY_DSN = os.environ.get("SENTRY_DSN")
    if SENTRY_DSN:
        sentry_sdk.init(
            dsn=SENTRY_DSN,
            integrations=[FlaskIntegration()],
            traces_sample_rate=1.0,
            profiles_sample_rate=1.0,
            send_default_pii=False  # PII disabled for privacy
        )
        app.logger.info("Sentry initialized.")
    else:
        app.logger.warning("SENTRY_DSN not set. Sentry not initialized.")

    # --- CORS Configuration ---
    CORS(app, resources={r"/*": {"origins": [
        "https://front-endnew.onrender.com",
        "http://127.0.0.1:5500",
        "http://localhost:5500"
    ]}})

    # --- Firebase Initialization ---
    try:
        firebase_json = os.environ.get("FIREBASE_CREDENTIALS")
        if not firebase_json:
            raise ValueError("FIREBASE_CREDENTIALS environment variable not set.")
        firebase_dict = json.loads(firebase_json)
        if not firebase_admin._apps:
            cred = credentials.Certificate(firebase_dict)
            firebase_admin.initialize_app(cred)
            app.logger.info("Firebase app initialized.")
    except Exception as e:
        app.logger.critical(f"CRITICAL: Firebase initialization failed: {e}")
        raise

    db = firestore.client()

    # --- Helper Functions (to be shared with blueprints) ---
    def get_real_ip():
        if 'X-Forwarded-For' in request.headers:
            return request.headers['X-Forwarded-For'].split(',')[0].strip()
        return request.remote_addr

    def send_notification_email(recipient_email, subject, body):
        APP_PASSWORD = os.environ.get('EMAIL_APP_PASSWORD')
        SENDER_EMAIL = os.environ.get('SENDER_EMAIL', 'itsmealwin12@gmail.com')
        if not APP_PASSWORD:
            app.logger.warning("EMAIL_APP_PASSWORD not set. Cannot send email.")
            return False
        
        msg = MIMEMultipart()
        msg['From'], msg['To'], msg['Subject'] = SENDER_EMAIL, recipient_email, subject
        msg.attach(MIMEText(body, 'plain'))
        
        try:
            with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
                server.login(SENDER_EMAIL, APP_PASSWORD)
                server.send_message(msg)
            app.logger.info(f"Email sent to {recipient_email}")
            return True
        except Exception as e:
            app.logger.error(f"Email sending failed: {e}", exc_info=True)
            return False

    def verify_admin_token(id_token):
        try:
            decoded_token = auth.verify_id_token(id_token)
            user_doc = db.collection('users').document(decoded_token['uid']).get()
            return user_doc.exists and user_doc.to_dict().get('role') == 'Admin', decoded_token.get('uid')
        except Exception as e:
            app.logger.error(f"Token verification failed: {e}", exc_info=True)
        return False, None

    # --- Connect Helpers to Blueprints ---
    # This makes the functions available within the blueprint files
    admin_bp.verify_admin_token_wrapper = verify_admin_token
    data_bp.send_notification_email = send_notification_email
    
    # --- Register Blueprints ---
    app.register_blueprint(auth_bp)
    app.register_blueprint(data_bp)
    app.register_blueprint(forecasting_bp)
    app.register_blueprint(admin_bp)

    # --- App Check Verification (Global) ---
    @app.before_request
    def verify_app_check_token():
        # FIX: Explicitly allow all OPTIONS requests for CORS preflight
        if request.method == 'OPTIONS':
            return None # Let Flask-Cors handle the response

        if request.path == '/':
            return None # Don't run checks on the root path

        app_check_token = request.headers.get('X-Firebase-AppCheck')
        if not app_check_token:
            return jsonify({'error': 'Unauthorized: App Check token missing'}), 401
        try:
            app_check.verify_token(app_check_token)
        except Exception as e:
            app.logger.error(f"App Check verification failed: {e}")
            return jsonify({'error': 'Unauthorized'}), 401

    # --- Root Endpoint ---
    @app.route('/')
    def index():
        return "✅ LINAC QA Backend Running (Refactored)"

    return app

# ==============================================================================
# --- WSGI ENTRY POINT ---
# ==============================================================================

app = create_app()

if __name__ == '__main__':
    # This runs the app in debug mode for local development
    app.run(debug=True, port=5000)
