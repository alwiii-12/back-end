from flask import Flask, request, jsonify
from flask_cors import CORS
import logging
import jwt
import os
import sentry_sdk
from sentry_sdk.integrations.flask import FlaskIntegration

# Import our custom initializers and modules
from .services.firebase import init_firebase, app_check_module

def create_app():
    """Application Factory Function"""
    
    # Initialize Firebase Admin SDK first
    init_firebase()

    app = Flask(__name__)
    
    # --- Sentry Configuration ---
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

    # --- CORS Configuration ---
    origins = [
        "https://front-endnew.onrender.com",
        "http://127.0.0.1:5500",
        "http://localhost:5500"
    ]
    CORS(app, resources={r"/*": {"origins": origins}})
    app.logger.setLevel(logging.DEBUG)

    # --- App Check Verification (runs before every request) ---
    @app.before_request
    def verify_app_check_token():
        # List of public paths that do not require App Check
        public_paths = ['/', '/public/groups', '/public/institutions-by-group', '/public/all-institutions']
        
        if request.method == 'OPTIONS' or request.path in public_paths:
            return None
            
        app_check_token = request.headers.get('X-Firebase-AppCheck')
        if not app_check_token:
            app.logger.warning("App Check token missing.")
            return jsonify({'error': 'Unauthorized: App Check token is missing'}), 401
        try:
            app_check_module.verify_token(app_check_token)
            return None
        except (ValueError, jwt.exceptions.DecodeError) as e:
            app.logger.error(f"Invalid App Check token: {e}")
            return jsonify({'error': f'Unauthorized: Invalid App Check token'}), 401
        except Exception as e:
            app.logger.error(f"App Check verification failed with an unexpected error: {e}")
            return jsonify({'error': 'Unauthorized: App Check verification failed'}), 401

    # --- Register Blueprints ---
    # We import the route modules here to avoid circular dependencies
    from .routes import public, auth, data, admin
    
    app.register_blueprint(public.bp)
    app.register_blueprint(auth.bp)
    app.register_blueprint(data.bp)
    app.register_blueprint(admin.bp)

    # A simple route to confirm the app is running
    @app.route('/')
    def index():
        return "✅ LINAC QA Backend Running (Refactored)"

    return app
