import firebase_admin
from firebase_admin import credentials, firestore, auth, app_check
import json
import os

# These will be initialized by the app factory
db = None
auth_module = None
app_check_module = None

def init_firebase():
    """Initializes the Firebase Admin SDK and sets up global service variables."""
    global db, auth_module, app_check_module
    
    # Check if the app is already initialized to prevent errors
    if not firebase_admin._apps:
        firebase_json = os.environ.get("FIREBASE_CREDENTIALS")
        if not firebase_json:
            raise Exception("CRITICAL: FIREBASE_CREDENTIALS environment variable not set")
        
        firebase_dict = json.loads(firebase_json)
        cred = credentials.Certificate(firebase_dict)
        firebase_admin.initialize_app(cred)
        print("Firebase default app initialized.")
    
    # Set the global variables for other modules to import
    db = firestore.client()
    auth_module = auth
    app_check_module = app_check
