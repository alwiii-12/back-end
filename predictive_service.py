import firebase_admin
from firebase_admin import credentials, firestore
import pandas as pd
from statsmodels.tsa.arima.model import ARIMA
import joblib
import os
import json
from calendar import monthrange

# --- INITIALIZE FIREBASE ADMIN (copy from your app.py) ---
# Ensure you have your FIREBASE_CREDENTIALS environment variable set
firebase_json = os.environ.get("FIREBASE_CREDENTIALS")
if not firebase_json:
    raise Exception("CRITICAL: FIREBASE_CREDENTIALS environment variable not set.")
firebase_dict = json.loads(firebase_json)

if not firebase_admin._apps:
    cred = credentials.Certificate(firebase_dict)
    firebase_admin.initialize_app(cred)
    print("Firebase default app initialized for predictive service.")

db = firestore.client()

# --- DATA FETCHING FUNCTION ---
def fetch_historical_data(center_id, data_type, energy_type):
    """
    Fetches all historical data for a specific metric from Firestore 
    and returns it as a clean time-series pandas DataFrame.
    """
    print(f"Fetching data for {center_id}, {data_type}, {energy_type}...")
    months_ref = db.collection("linac_data").document(center_id).collection("months").stream()
    
    all_values = []

    for month_doc in months_ref:
        month_data = month_doc.to_dict()
        field_name = f"data_{data_type}"
        
        if field_name in month_data:
            month_id_str = month_doc.id.replace("Month_", "")
            year, mon = map(int, month_id_str.split("-"))
            
            for row_data in month_data[field_name]:
                if row_data.get("energy") == energy_type:
                    # Create a date for each value in the array
                    for i, value in enumerate(row_data.get("values", [])):
                        day = i + 1
                        try:
                            # Ensure the day exists for the given month
                            monthrange(year, mon)
                            date = pd.to_datetime(f"{year}-{mon}-{day}")
                            # Convert value to float, skip if invalid
                            float_value = float(value)
                            all_values.append({"date": date, "value": float_value})
                        except (ValueError, TypeError):
                            # Skip invalid values or dates
                            continue
    
    if not all_values:
        print("No data found.")
        return pd.DataFrame()

    # Convert to DataFrame, sort by date, and set date as index
    df = pd.DataFrame(all_values)
    df = df.sort_values(by="date").set_index("date")
    
    # Ensure no duplicate dates, taking the mean if they exist
    df = df.groupby(df.index).mean()

    print(f"Successfully fetched {len(df)} data points.")
    return df

# --- MODEL TRAINING FUNCTION ---
def train_and_save_model(data_df, model_path):
    """
    Trains an ARIMA model and saves it to a file.
    """
    if data_df.empty or len(data_df) < 20: # Need sufficient data to train
        print("Not enough data to train model. Minimum 20 data points required.")
        return

    print("Training ARIMA model...")
    # The order (p,d,q) is a key parameter. (5,1,0) is a common starting point.
    # p: The number of lag observations in the model (lag order).
    # d: The number of times the raw observations are differenced (degree of differencing).
    # q: The size of the moving average window (order of moving average).
    model = ARIMA(data_df['value'], order=(5, 1, 0))
    model_fit = model.fit()
    
    print("Saving model to:", model_path)
    joblib.dump(model_fit, model_path)
    print("Model saved successfully.")
    
    return model_fit

# --- MAIN EXECUTION BLOCK ---
if __name__ == '__main__':
    # --- Parameters for the model you want to train ---
    # Replace these with a centerId, dataType, and energyType from your database
    TARGET_CENTER_ID = "aoi_gurugram" 
    TARGET_DATA_TYPE = "output"
    TARGET_ENERGY_TYPE = "6X"

    # Fetch the data
    historical_df = fetch_historical_data(TARGET_CENTER_ID, TARGET_DATA_TYPE, TARGET_ENERGY_TYPE)
    
    if not historical_df.empty:
        # Define where to save the model file
        # Creating a directory for models is a good practice
        if not os.path.exists('models'):
            os.makedirs('models')
        
        model_filename = f"models/model_{TARGET_CENTER_ID}_{TARGET_DATA_TYPE}_{TARGET_ENERGY_TYPE}.pkl"
        
        # Train and save the model
        train_and_save_model(historical_df, model_filename)
