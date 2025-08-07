import firebase_admin
from firebase_admin import credentials, firestore
import pandas as pd
from prophet import Prophet
import os
import json
from calendar import monthrange
from datetime import datetime

# --- INITIALIZE FIREBASE ADMIN ---
try:
    if 'FIREBASE_CREDENTIALS' in os.environ:
        creds_json = json.loads(os.environ.get('FIREBASE_CREDENTIALS'))
        cred = credentials.Certificate(creds_json)
        print("Firebase credentials loaded from environment variable.")
    else:
        cred = credentials.Certificate("firebase_credentials.json")
        print("Firebase credentials file found locally.")
except Exception as e:
    print(f"Could not initialize Firebase: {e}")
    raise Exception("CRITICAL: Ensure Firebase credentials are set up correctly.")

if not firebase_admin._apps:
    firebase_admin.initialize_app(cred)
    print("Firebase default app initialized for predictive service.")

db = firestore.client()

# --- FUNCTION TO FETCH SERVICE EVENTS (Unchanged) ---
def fetch_service_events(center_id):
    """
    Fetches all marked service/calibration dates for a specific center (hospital).
    """
    events = []
    users_ref = db.collection('users').where('centerId', '==', center_id).limit(1).stream()
    user_uid = None
    for user in users_ref:
        user_uid = user.id
        break

    if not user_uid:
        print(f"No user found for centerId: {center_id}")
        return None

    events_ref = db.collection('service_events').document(user_uid).collection('events').stream()
    for event in events_ref:
        events.append(event.id) 
    
    if not events:
        print("No service/calibration events found.")
        return None

    holidays_df = pd.DataFrame({
        'holiday': 'service_day',
        'ds': pd.to_datetime(events),
        'lower_window': 0,
        'upper_window': 1,
    })
    print(f"Found {len(events)} service/calibration events for user {user_uid}.")
    return holidays_df

# --- DATA FETCHING FUNCTION (Unchanged) ---
def fetch_all_historical_data(center_id, data_type, energy_type):
    """
    Fetches ALL historical data up to the current date for a given combination.
    """
    print(f"Fetching all historical data for {center_id}, {data_type}, {energy_type}...")
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
                            if value and day <= monthrange(year, mon)[1]:
                                date = pd.to_datetime(f"{year}-{mon}-{day}")
                                float_value = float(value)
                                all_values.append({"ds": date, "y": float_value})
                        except (ValueError, TypeError):
                            continue
    
    if not all_values:
        return pd.DataFrame()

    df = pd.DataFrame(all_values).sort_values(by="ds").drop_duplicates(subset='ds', keep='last')
    return df

# --- [REFACTORED] MODEL TRAINING & PREDICTION ---
def train_and_predict(full_df, service_events_df):
    """
    [REFACTORED] Trains one model on ALL data and generates a forecast for the next 12 months.
    """
    if full_df.empty or len(full_df) < 10:
        print(f"Not enough data to train. Found {len(full_df)} records. Skipping.")
        return None

    print(f"Training model on {len(full_df)} data points...")
    
    model = Prophet(holidays=service_events_df)
    model.fit(full_df)
    
    # Create a future dataframe to predict for the next 365 days
    future = model.make_future_dataframe(periods=365)

    print("Generating 365-day forecast...")
    forecast = model.predict(future)
    
    return forecast

# --- SAVE PREDICTION TO FIRESTORE (Unchanged) ---
def save_monthly_prediction(center_id, data_type, energy_type, month_key, forecast_chunk):
    """
    Saves a month-specific forecast to Firestore.
    """
    forecast_data = []
    # Use the provided forecast_chunk instead of the full forecast
    for _, row in forecast_chunk.iterrows():
        forecast_data.append({
            "date": row['ds'].strftime('%Y-%m-%d'),
            "predicted_value": row['yhat'],
            "lower_bound": row['yhat_lower'],
            "upper_bound": row['yhat_upper']
        })

    prediction_doc_id = f"{center_id}_{data_type}_{energy_type}_{month_key}"
    doc_ref = db.collection("linac_predictions").document(prediction_doc_id)
    
    doc_ref.set({
        "centerId": center_id,
        "dataType": data_type,
        "energy": energy_type,
        "forecastMonth": month_key,
        "forecast": forecast_data,
        "lastUpdated": firestore.SERVER_TIMESTAMP
    })
    print(f"Successfully saved forecast for {month_key} to Firestore.")

# --- [REFACTORED] MAIN EXECUTION BLOCK ---
if __name__ == '__main__':
    HOSPITAL_IDS_TO_PROCESS = [
        "aoi_gurugram", "medanta_gurugram", "fortis_delhi", "apollo_chennai", "max_delhi"
    ]
    DATA_TYPES_TO_PROCESS = ["output", "flatness", "inline", "crossline"]
    ENERGY_TYPES_TO_PROCESS = [
        "6X", "10X", "15X", "6X FFF", "10X FFF", "6E", 
        "9E", "12E", "15E", "18E"
    ]

    for hospital_id in HOSPITAL_IDS_TO_PROCESS:
        service_events = fetch_service_events(hospital_id)

        for data_type in DATA_TYPES_TO_PROCESS:
            for energy in ENERGY_TYPES_TO_PROCESS:
                print(f"\n--- Processing: {hospital_id} / {data_type} / {energy} ---")
                
                # Fetch all historical data ONCE
                all_data_df = fetch_all_historical_data(hospital_id, data_type, energy)
                
                if all_data_df.empty:
                    print("No data found, skipping.")
                    continue

                # [REFACTORED] Train model ONCE
                full_forecast = train_and_predict(all_data_df, service_events)

                if full_forecast is not None:
                    # [REFACTORED] Group the single large forecast into monthly chunks
                    # and save each chunk to a separate document in Firestore.
                    
                    last_historical_date = all_data_df['ds'].max()
                    
                    # [THE FIX] Use >= to include predictions for the current month
                    future_predictions = full_forecast[full_forecast['ds'] >= last_historical_date]
                    
                    # Group predictions by month
                    future_predictions['month_key'] = future_predictions['ds'].dt.strftime('%Y-%m')
                    
                    # Get all unique months that have predictions
                    months_to_save = future_predictions['month_key'].unique()

                    for month in months_to_save:
                        # Get the slice of the forecast for this specific month
                        monthly_forecast_chunk = future_predictions[future_predictions['month_key'] == month]
                        
                        # We only need a 7-day forecast for the frontend display
                        # We will save the first 7 days of prediction for each month
                        final_chunk_to_save = monthly_forecast_chunk.head(7)

                        save_monthly_prediction(hospital_id, data_type, energy, month, final_chunk_to_save)
    
    print("\n\n--- All forecasts processed. Batch complete. ---")
