import firebase_admin
from firebase_admin import credentials, firestore
import pandas as pd
from prophet import Prophet
import joblib
import os
import json
from calendar import monthrange

# --- INITIALIZE FIREBASE ADMIN ---
try:
    cred = credentials.Certificate("firebase_credentials.json")
    print("Firebase credentials file found locally.")
except Exception as e:
    print(f"Could not initialize from local file: {e}")
    raise Exception("CRITICAL: Make sure 'firebase_credentials.json' is in the same folder as the script.")

if not firebase_admin._apps:
    firebase_admin.initialize_app(cred)
    print("Firebase default app initialized for predictive service.")

db = firestore.client()

# --- DATA FETCHING FUNCTION (Updated for Prophet) ---
def fetch_historical_data(center_id, data_type, energy_type):
    """
    Fetches historical data and formats it for Prophet (columns 'ds' and 'y').
    """
    print(f"Fetching data for {center_id}, {data_type}, {energy_type}...")
    months_ref = db.collection("linac_data").document(center_id).collection("months").stream()
    
    all_values = []
    for month_doc in months_ref:
        # ... (data fetching logic is the same)
        month_data = month_doc.to_dict()
        field_name = f"data_{data_type}"
        if field_name in month_data:
            month_id_str = month_doc.id.replace("Month_", "")
            year, mon = map(int, month_id_str.split("-"))
            for row_data in month_data[field_name]:
                if row_data.get("energy") == energy_type:
                    for i, value in enumerate(row_data.get("values", [])):
                        day = i + 1
                        try:
                            monthrange(year, mon)
                            # Prophet requires column names 'ds' for date and 'y' for value
                            date = pd.to_datetime(f"{year}-{mon}-{day}")
                            float_value = float(value)
                            all_values.append({"ds": date, "y": float_value})
                        except (ValueError, TypeError):
                            continue
    
    if not all_values:
        print("No data found for this energy type.")
        return pd.DataFrame()

    df = pd.DataFrame(all_values).sort_values(by="ds")
    print(f"Successfully fetched {len(df)} data points.")
    return df

# --- MODEL TRAINING FUNCTION (Updated for Prophet) ---
def train_and_save_model(data_df, model_path):
    """
    Trains a Prophet model and saves it to a file.
    """
    if data_df.empty or len(data_df) < 20:
        print("Not enough data to train model. Minimum 20 data points required.")
        return None

    print("Training Prophet model...")
    # Prophet is instantiated and then fit to the data
    model = Prophet()
    model.fit(data_df)
    
    print("Saving model to:", model_path)
    joblib.dump(model, model_path)
    print("Model saved successfully.")
    return model

# --- PREDICTION GENERATION FUNCTION (Updated for Prophet) ---
def generate_and_save_predictions(center_id, data_type, energy_type, model_path):
    """
    Loads a trained Prophet model, generates a 7-day forecast, and saves it.
    """
    print(f"Generating predictions for {energy_type}...")
    try:
        model = joblib.load(model_path)
    except FileNotFoundError:
        print(f"Model file not found at {model_path}. Skipping prediction.")
        return

    # 1. Create a dataframe for future dates
    future = model.make_future_dataframe(periods=7)
    # 2. Generate the forecast
    forecast = model.predict(future)

    # 3. Extract the 7 future prediction rows
    future_forecast = forecast.tail(7)

    forecast_data = []
    for index, row in future_forecast.iterrows():
        forecast_data.append({
            "date": row['ds'].strftime('%Y-%m-%d'),
            "predicted_value": row['yhat'],
            "lower_bound": row['yhat_lower'],
            "upper_bound": row['yhat_upper']
        })

    prediction_doc_id = f"{center_id}_{data_type}_{energy_type}"
    doc_ref = db.collection("linac_predictions").document(prediction_doc_id)
    
    doc_ref.set({
        "centerId": center_id,
        "dataType": data_type,
        "energy": energy_type,
        "forecast": forecast_data,
        "lastUpdated": firestore.SERVER_TIMESTAMP
    })
    print(f"Successfully saved 7-day forecast for {energy_type} to Firestore.")

# --- MAIN EXECUTION BLOCK (Updated for Prophet) ---
if __name__ == '__main__':
    HOSPITAL_IDS_TO_PROCESS = [
        "aoi_gurugram",
        "medanta_gurugram",
        "fortis_delhi",
        "apollo_chennai",
        "max_delhi"
    ]
    TARGET_DATA_TYPE = "output"
    ENERGY_TYPES_TO_TRAIN = [
        "6X", "10X", "15X", "6X FFF", "10X FFF", "6E", 
        "9E", "12E", "15E", "18E"
    ]

    for hospital_id in HOSPITAL_IDS_TO_PROCESS:
        print(f"\n========================================================")
        print(f"--- Starting batch processing for HOSPITAL: {hospital_id} ---")
        print(f"========================================================")
        
        for energy in ENERGY_TYPES_TO_TRAIN:
            print(f"\n--- Processing Energy: {energy} ---")
            
            historical_df = fetch_historical_data(hospital_id, TARGET_DATA_TYPE, energy)
            
            if not historical_df.empty:
                if not os.path.exists('models'):
                    os.makedirs('models')
                model_filename = f"models/model_{hospital_id}_{TARGET_DATA_TYPE}_{energy}.pkl"
                
                trained_model = train_and_save_model(historical_df, model_filename)

                if trained_model:
                    generate_and_save_predictions(hospital_id, TARGET_DATA_TYPE, energy, model_filename)
    
    print("\n\n--- All hospitals processed. Batch complete. ---")
