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
    """Creates and newfigures the Flask application."""
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
            send_default_pii=False
        )
        app.logger.info("Sentry initialized.")
    else:
        app.logger.warning("SENTRY_DSN not set. Sentry not initialized.")

    # --- CORS newfiguration ---
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

    # --- Helper Functions ---
    def verify_admin_token(id_token):
        try:
            decoded_token = auth.verify_id_token(id_token)
            user_doc = db.collection('users').document(decoded_token['uid']).get()
            return user_doc.exists and user_doc.to_dict().get('role') == 'Admin', decoded_token.get('uid')
        except Exception as e:
            app.logger.error(f"Token verification failed: {e}", exc_info=True)
        return False, None

    # --- Connect Helpers to Blueprints ---
    admin_bp.verify_admin_token_wrapper = verify_admin_token
    # In a larger app, you might pass a mailer object instead of the function itself
    # data_bp.send_notification_email = send_notification_email
    
    # --- Register Blueprints ---
    app.register_blueprint(auth_bp)
    app.register_blueprint(data_bp)
    app.register_blueprint(forecasting_bp)
    app.register_blueprint(admin_bp)

    # --- App Check Verification (Global) ---
    @app.before_request
    def verify_app_check_token():
        if request.method == 'OPTIONS':
            return None
        if request.path in ['/', '/dashboard-summary']: # Exempt specific paths if needed
            return None
        app_check_token = request.headers.get('X-Firebase-AppCheck')
        if not app_check_token:
            return jsonify({'error': 'Unauthorized: App Check token missing'}), 401
        try:
            app_check.verify_token(app_check_token)
        except Exception as e:
            app.logger.error(f"App Check verification failed: {e}")
            return jsonify({'error': 'Unauthorized'}), 401

    # --- Root & Other Main Endpoints ---
    @app.route('/')
    def index():
        return "✅ LINAC QA Backend Running (Refactored)"

    @app.route('/dashboard-summary', methods=['GET'])
    def get_dashboard_summary():
        # This is a placeholder implementation. You would build this out
        # with real data queries to count warnings, OOTs, etc.
        try:
            # Example logic for fetching pending users
            pending_users = db.collection('users').where('status', '==', 'pending').stream()
            pending_count = len(list(pending_users))

            # Placeholder data for the rest
            summary_data = {
                "role": "Admin", # This would be determined from the user's token
                "pending_users_count": pending_count,
                "total_warnings": 15, # Placeholder
                "total_oot": 4,       # Placeholder
                "leaderboard": [
                    {"hospital": "aoi_gurugram", "warnings": 5, "oot": 2},
                    {"hospital": "medanta_gurugram", "warnings": 3, "oot": 1},
                    {"hospital": "max_delhi", "warnings": 7, "oot": 1},
                ]
            }
            return jsonify(summary_data), 200
        except Exception as e:
            app.logger.error(f"Dashboard summary failed: {e}", exc_info=True)
            return jsonify({'message': 'Failed to load dashboard summary'}), 500


    return app

# ==============================================================================
# --- WSGI ENTRY POINT ---
# ==============================================================================
app = create_app()

if __name__ == '__main__':
    app.run(debug=True, port=5000)
