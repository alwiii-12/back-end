import os
import sentry_sdk
from sentry_sdk.integrations.flask import FlaskIntegration
from flask import Flask, request, jsonify
from flask_cors import CORS
import logging
import jwt

# Import from the new services and routes structure
from .services.firebase import init_firebase, app_check_module
from .routes import auth, data, public, admin

def create_app():
    """Create and configure an instance of the Flask application."""
    app = Flask(__name__)

    # --- Sentry Initialization ---
    SENTRY_DSN = os.environ.get("SENTRY_DSN")
    if SENTRY_DSN:
        sentry_sdk.init(
            dsn=SENTRY_DSN,
            integrations=[FlaskIntegration()],
            traces_sample_rate=1.0,
            profiles_sample_rate=1.0,
            send_default_pii=True
        )
        print("Sentry initialized successfully.")
    else:
        print("SENTRY_DSN environment variable not set. Sentry not initialized.")

    # --- [CORRECTED] CORS Configuration ---
    # This list now includes ALL of your frontend URLs to fix the errors.
    origins = [
        "https://front-endnew.onrender.com",   # From your last screenshot
        "https://host-withdraw.onrender.com", # From your previous screenshot
        "http://127.0.0.1:5500",               # For local testing
        "http://localhost:5500"                 # For local testing
    ]
    CORS(app, resources={r"/*": {"origins": origins}})
    app.logger.setLevel(logging.INFO)

    # --- Initialize Services ---
    init_firebase()

    # --- App Check Verification ---
    @app.before_request
    def verify_app_check_token():
        # Public paths exempt from App Check
        public_paths = ['/', '/public/groups', '/public/institutions-by-group', '/public/all-institutions']
        if request.method == 'OPTIONS' or request.path in public_paths:
            return None
            
        app_check_token = request.headers.get('X-Firebase-AppCheck')
        if not app_check_token:
            app.logger.warning("App Check token missing.")
            return jsonify({'error': 'Unauthorized: App Check token is missing'}), 401
        try:
            app_check_module.verify_token(app_check_token)
        except (ValueError, jwt.exceptions.DecodeError) as e:
            app.logger.error(f"Invalid App Check token: {e}")
            return jsonify({'error': 'Unauthorized: Invalid App Check token'}), 401
        except Exception as e:
            app.logger.error(f"App Check verification failed unexpectedly: {e}")
            sentry_sdk.capture_exception(e)
            return jsonify({'error': 'Unauthorized: App Check verification failed'}), 401

    # --- Register Blueprints ---
    app.register_blueprint(auth.bp)
    app.register_blueprint(data.bp)
    app.register_blueprint(public.bp)
    app.register_blueprint(admin.bp)

    # --- Root Endpoint ---
    @app.route('/')
    def index():
        return "✅ LINAC QA Backend Running"

    return app
