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
firebase_json = os.environ.get("FIREBASE_CREDENTIALS")
if not firebase_json:
    raise Exception("CRITICAL: FIREBASE_CREDENTIALS environment variable not set.")
firebase_dict = json.loads(firebase_json)

if not firebase_admin._apps:
    cred = credentials.Certificate(firebase_dict)
    firebase_admin.initialize_app(cred)
    print("Firebase default app initialized for predictive service.")

db = firestore.client()

# --- DATA FETCHING FUNCTION (Unchanged) ---
def fetch_historical_data(center_id, data_type, energy_type):
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

# --- MODEL TRAINING FUNCTION (Unchanged) ---
def train_and_save_model(data_df, model_path):
    if data_df.empty or len(data_df) < 20:
        print("Not enough data to train model. Minimum 20 data points required.")
        return None
    print("Training ARIMA model...")
    model = ARIMA(data_df['value'], order=(5, 1, 0))
    model_fit = model.fit()
    print("Saving model to:", model_path)
    joblib.dump(model_fit, model_path)
    print("Model saved successfully.")
    return model_fit

# --- NEW: PREDICTION GENERATION FUNCTION ---
def generate_and_save_predictions(center_id, data_type, energy_type, model_path):
    """
    Loads a trained model, generates a 7-day forecast,
    and saves it to the 'linac_predictions' collection in Firestore.
    """
    print(f"Generating predictions for {energy_type}...")
    try:
        # Load the pre-trained model from the file
        model_fit = joblib.load(model_path)
    except FileNotFoundError:
        print(f"Model file not found at {model_path}. Skipping prediction.")
        return

    # Generate a 7-day forecast
    forecast_result = model_fit.get_forecast(steps=7)
    
    # Get the predicted values and the confidence intervals
    predicted_mean = forecast_result.predicted_mean
    confidence_intervals = forecast_result.conf_int()

    # Prepare the data for Firestore
    forecast_data = []
    for i, date in enumerate(predicted_mean.index):
        forecast_data.append({
            "date": date.strftime('%Y-%m-%d'),
            "predicted_value": predicted_mean.iloc[i],
            "lower_bound": confidence_intervals.iloc[i, 0],
            "upper_bound": confidence_intervals.iloc[i, 1]
        })

    # Save the forecast to Firestore
    # The document ID makes it easy to find predictions for a specific combination
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

# --- MAIN EXECUTION BLOCK (Updated) ---
if __name__ == '__main__':
    TARGET_CENTER_ID = "aoi_gurugram" 
    TARGET_DATA_TYPE = "output"
    ENERGY_TYPES_TO_TRAIN = [
        "6X", "10X", "15X", "6X FFF", "10X FFF", "6E", 
        "9E", "12E", "15E", "18E"
    ]

    print(f"--- Starting batch processing for {TARGET_CENTER_ID} - {TARGET_DATA_TYPE} ---")

    for energy in ENERGY_TYPES_TO_TRAIN:
        print(f"\n--- Processing Energy: {energy} ---")
        historical_df = fetch_historical_data(TARGET_CENTER_ID, TARGET_DATA_TYPE, energy)
        
        if not historical_df.empty:
            if not os.path.exists('models'):
                os.makedirs('models')
            model_filename = f"models/model_{TARGET_CENTER_ID}_{TARGET_DATA_TYPE}_{energy}.pkl"
            
            # 1. Train and save the model
            train_and_save_model(historical_df, model_filename)

            # 2. Use the newly saved model to generate and save predictions
            generate_and_save_predictions(TARGET_CENTER_ID, TARGET_DATA_TYPE, energy, model_filename)
    
    print("\n--- Batch processing complete. ---")
