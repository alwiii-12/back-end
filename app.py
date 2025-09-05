import os
import json
import logging
from flask import Flask, request, jsonify
from flask_cors import CORS
import firebase_admin
from firebase_admin import credentials, firestore, auth, app_check

# --- BLUEPRINT IMPORTS ---
from blueprints.auth import auth_bp
from blueprints.data import data_bp
from blueprints.forecasting import forecasting_bp
from blueprints.admin import admin_bp

def create_app():
    """Creates and configures the Flask application."""
    app = Flask(__name__)
    app.logger.setLevel(logging.DEBUG)

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
            app.logger.info("Firebase app initialized successfully.")
    except Exception as e:
        app.logger.critical(f"CRITICAL: Firebase initialization failed: {e}")
        raise

    # --- Helper Function for Admin Auth ---
    def verify_admin_token(id_token):
        # FIX: Gets the Firestore client inside the function to ensure the app is initialized.
        db = firestore.client()
        try:
            decoded_token = auth.verify_id_token(id_token)
            user_doc = db.collection('users').document(decoded_token['uid']).get()
            return user_doc.exists and user_doc.to_dict().get('role') == 'Admin', decoded_token.get('uid')
        except Exception as e:
            app.logger.error(f"Token verification failed: {e}", exc_info=True)
        return False, None

    # --- Connect Helpers and Register Blueprints ---
    admin_bp.verify_admin_token_wrapper = verify_admin_token
    app.register_blueprint(auth_bp)
    app.register_blueprint(data_bp)
    app.register_blueprint(forecasting_bp)
    app.register_blueprint(admin_bp)

    # --- App Check Verification (Global) ---
    @app.before_request
    def verify_app_check_token():
        if request.method == 'OPTIONS':
            return None
        if request.path in ['/', '/dashboard-summary']: # Exempt public paths
            return None
            
        app_check_token = request.headers.get('X-Firebase-AppCheck')
        if not app_check_token:
            return jsonify({'error': 'Unauthorized: App Check token missing'}), 401
        try:
            app_check.verify_token(app_check_token)
        except Exception as e:
            app.logger.error(f"App Check verification failed: {e}")
            return jsonify({'error': 'Unauthorized'}), 401

    @app.route('/')
    def index():
        return "✅ LINAC QA Backend is fully operational."
    
    # Placeholder for the /dashboard-summary endpoint
    @app.route('/dashboard-summary', methods=['GET'])
    def get_dashboard_summary():
        return jsonify({"message": "Summary data placeholder"}), 200


    return app

app = create_app()

if __name__ == '__main__':
    app.run(debug=True, port=5000)
