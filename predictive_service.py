import firebase_admin
from firebase_admin import credentials, firestore
import pandas as pd
from prophet import Prophet
import joblib
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

# --- FUNCTION TO FETCH SERVICE EVENTS ---
def fetch_service_events(hospital_id):
    """
    Fetches all marked service/calibration dates for a specific hospital.
    """
    events = []
    # Find the user UID associated with the hospital's centerId
    users_ref = db.collection('users').where('centerId', '==', hospital_id).limit(1).stream()
    user_uid = None
    for user in users_ref:
        user_uid = user.id
        break

    if not user_uid:
        print(f"No user found for centerId: {hospital_id}, cannot fetch service events.")
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

# --- DATA FETCHING FUNCTION ---
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
        return pd.DataFrame(), []

    df = pd.DataFrame(all_values).sort_values(by="ds").drop_duplicates(subset='ds', keep='last')
    unique_months = df['ds'].dt.strftime('%Y-%m').unique()
    return df, unique_months

# --- MODEL TRAINING & PREDICTION ---
def train_and_predict_for_month(full_df, month_to_forecast, service_events_df):
    """
    Trains a model on data up to a specific month and generates a 7-day forecast.
    """
    end_of_month = pd.to_datetime(month_to_forecast) + pd.offsets.MonthEnd(0)
    df_for_training = full_df[full_df['ds'] <= end_of_month]

    if df_for_training.empty or len(df_for_training) < 10:
        print(f"Not enough data to train for month {month_to_forecast}. Skipping.")
        return None

    print(f"Training model for data up to {month_to_forecast}...")
    model = Prophet(holidays=service_events_df)
    model.fit(df_for_training)
    
    last_date_in_data = df_for_training['ds'].iloc[-1]
    future = pd.date_range(start=last_date_in_data, periods=8)[1:]
    future_df = pd.DataFrame({'ds': future})

    print(f"Generating 7-day forecast starting after {last_date_in_data.strftime('%Y-%m-%d')}...")
    forecast = model.predict(future_df)
    return forecast

# --- SAVE PREDICTION TO FIRESTORE ---
def save_monthly_prediction(center_id, data_type, energy_type, month_key, forecast):
    """
    Saves a month-specific forecast to Firestore.
    """
    forecast_data = []
    for _, row in forecast.iterrows():
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

# --- MAIN EXECUTION BLOCK (PRODUCTION VERSION) ---
if __name__ == '__main__':
    HOSPITAL_IDS_TO_PROCESS = [
        "aoi_gurugram",
        "medanta_gurugram",
        "fortis_delhi",
        "apollo_chennai",
        "max_delhi"
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
                
                all_data_df, unique_months = fetch_all_historical_data(hospital_id, data_type, energy)
                
                if all_data_df.empty:
                    print(f"No data found for {energy}, skipping.")
                    continue

                for month in unique_months:
                    print(f"\n--- Generating forecast for month: {month} ---")
                    
                    forecast_df = train_and_predict_for_month(all_data_df, month, service_events)
                    
                    if forecast_df is not None:
                        save_monthly_prediction(hospital_id, data_type, energy, month, forecast_df)

    print("\n\n--- All monthly forecasts processed. Batch complete. ---")
