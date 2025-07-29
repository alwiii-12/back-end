import firebase_admin
from firebase_admin import credentials, firestore
import pandas as pd
from statsmodels.tsa.arima.model import ARIMA
import joblib
import os
import json
from calendar import monthrange
from datetime import timedelta

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
                    for i, value in enumerate(row_data.get("values", [])):
                        day = i + 1
                        try:
                            monthrange(year, mon)
                            date = pd.to_datetime(f"{year}-{mon}-{day}")
                            float_value = float(value)
                            all_values.append({"date": date, "value": float_value})
                        except (ValueError, TypeError):
                            continue
    
    if not all_values:
        print("No data found for this energy type.")
        return pd.DataFrame()

    df = pd.DataFrame(all_values).sort_values(by="date").set_index("date")
    df = df.groupby(df.index).mean()
    print(f"Successfully fetched {len(df)} data points.")
    return df

# --- MODEL TRAINING FUNCTION ---
def train_and_save_model(data_df, model_path):
    """
    Trains an ARIMA model, saves it, and returns the model and the last date.
    """
    if data_df.empty or len(data_df) < 20:
        print("Not enough data to train model. Minimum 20 data points required.")
        return None, None

    print("Training ARIMA model...")
    model = ARIMA(data_df['value'], order=(5, 1, 0))
    model_fit = model.fit()
    
    print("Saving model to:", model_path)
    joblib.dump(model_fit, model_path)
    print("Model saved successfully.")
    
    return model_fit, data_df.index[-1]

# --- PREDICTION GENERATION FUNCTION ---
def generate_and_save_predictions(center_id, data_type, energy_type, model_path, last_date):
    """
    Loads a trained model, generates a 7-day forecast using the provided last_date,
    and saves it to the 'linac_predictions' collection in Firestore.
    """
    print(f"Generating predictions for {energy_type}...")
    try:
        model_fit = joblib.load(model_path)
    except FileNotFoundError:
        print(f"Model file not found at {model_path}. Skipping prediction.")
        return

    forecast_steps = 7
    forecast_result = model_fit.get_forecast(steps=forecast_steps)
    predicted_mean = forecast_result.predicted_mean
    confidence_intervals = forecast_result.conf_int()

    forecast_data = []
    for i in range(forecast_steps):
        forecast_date = last_date + timedelta(days=i + 1)
        forecast_data.append({
            "date": forecast_date.strftime('%Y-%m-%d'),
            "predicted_value": predicted_mean.iloc[i],
            "lower_bound": confidence_intervals.iloc[i, 0],
            "upper_bound": confidence_intervals.iloc[i, 1]
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

# --- MAIN EXECUTION BLOCK ---
if __name__ == '__main__':
    # --- List all hospital IDs you want to process ---
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
        print(f"\n\n========================================================")
        print(f"--- Starting batch processing for HOSPITAL: {hospital_id} ---")
        print(f"========================================================")
        
        for energy in ENERGY_TYPES_TO_TRAIN:
            print(f"\n--- Processing Energy: {energy} ---")
            
            # This line has been corrected
            historical_df = fetch_historical_data(hospital_id, TARGET_DATA_TYPE, energy)
            
            if not historical_df.empty:
                if not os.path.exists('models'):
                    os.makedirs('models')
                model_filename = f"models/model_{hospital_id}_{TARGET_DATA_TYPE}_{energy}.pkl"
                
                trained_model, last_known_date = train_and_save_model(historical_df, model_filename)

                if trained_model and last_known_date:
                    generate_and_save_predictions(hospital_id, TARGET_DATA_TYPE, energy, model_filename, last_known_date)
    
    print("\n\n--- All hospitals processed. Batch complete. ---")
