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

# --- [MODIFIED] FUNCTION TO DYNAMICALLY FETCH MACHINES ---
def fetch_all_machine_ids():
    """Fetches all machineIds from the linacs collection."""
    print("Fetching all machine IDs from Firestore...")
    machines = []
    try:
        machines_ref = db.collection('linacs').stream()
        for machine in machines_ref:
            machine_data = machine.to_dict()
            machines.append({
                "machineId": machine.id,
                "centerId": machine_data.get("centerId")
            })
        print(f"Found {len(machines)} machines to process.")
        return machines
    except Exception as e:
        print(f"Error fetching machine IDs: {e}")
        return []

# --- [MODIFIED] FUNCTION TO DELETE ONLY FUTURE PREDICTIONS (MACHINE-AWARE) ---
def delete_future_predictions(machine_id):
    """
    Deletes predictions for future months for a specific machine to clean up before generating new ones.
    """
    print(f"Deleting future predictions for machine {machine_id}...")
    current_month_str = datetime.now().strftime('%Y-%m')
    
    # This query is tricky because the machineId is part of the document ID
    # We must fetch all and filter in Python.
    predictions_ref = db.collection("linac_predictions").stream()
    
    deleted_count = 0
    for doc in predictions_ref:
        if doc.id.startswith(machine_id):
            doc_data = doc.to_dict()
            forecast_month = doc_data.get("forecastMonth")
            if forecast_month and forecast_month > current_month_str:
                doc.reference.delete()
                deleted_count += 1
    
    print(f"Deleted {deleted_count} future prediction document(s) for machine {machine_id}.")


# --- [MODIFIED] FUNCTION TO FETCH SERVICE EVENTS (CENTER-AWARE) ---
def fetch_service_events(center_id):
    """
    Fetches all marked service/calibration dates for a specific center (hospital).
    """
    if not center_id:
        return None
        
    events = []
    users_ref = db.collection('users').where('centerId', '==', center_id).limit(1).stream()
    user_uid = None
    for user in users_ref:
        user_uid = user.id
        break

    if not user_uid:
        print(f"No user found for centerId: {center_id}, cannot fetch service events.")
        return None

    events_ref = db.collection('service_events').document(user_uid).collection('events').stream()
    for event in events_ref:
        events.append(event.id) 
    
    if not events:
        print(f"No service/calibration events found for center {center_id}.")
        return None

    holidays_df = pd.DataFrame({
        'holiday': 'service_day',
        'ds': pd.to_datetime(events),
        'lower_window': 0,
        'upper_window': 1,
    })
    print(f"Found {len(events)} service/calibration events for center {center_id}.")
    return holidays_df

# --- [MODIFIED] DATA FETCHING FUNCTION (MACHINE-AWARE) ---
def fetch_all_historical_data(machine_id, data_type, energy_type):
    """
    Fetches ALL historical data up to the current date for a given machine.
    """
    print(f"Fetching all historical data for Machine: {machine_id}, {data_type}, {energy_type}...")
    months_ref = db.collection("linac_data").document(machine_id).collection("months").stream()
    
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

# --- MODEL TRAINING & PREDICTION ---
def train_and_predict(full_df, service_events_df):
    if full_df.empty or len(full_df) < 10:
        print(f"Not enough data to train. Found {len(full_df)} records. Skipping.")
        return None

    print(f"Training model on {len(full_df)} data points...")
    
    model = Prophet(holidays=service_events_df)
    model.fit(full_df)
    
    future = model.make_future_dataframe(periods=365)

    print("Generating 365-day forecast...")
    forecast = model.predict(future)
    
    return forecast

# --- [MODIFIED] SAVE PREDICTION TO FIRESTORE (MACHINE-AWARE) ---
def save_monthly_prediction(machine_id, center_id, data_type, energy_type, month_key, forecast_chunk):
    forecast_data = []
    for _, row in forecast_chunk.iterrows():
        forecast_data.append({
            "date": row['ds'].strftime('%Y-%m-%d'),
            "predicted_value": row['yhat'],
            "lower_bound": row['yhat_lower'],
            "upper_bound": row['yhat_upper']
        })

    prediction_doc_id = f"{machine_id}_{data_type}_{energy_type}_{month_key}"
    doc_ref = db.collection("linac_predictions").document(prediction_doc_id)
    
    doc_ref.set({
        "machineId": machine_id,
        "centerId": center_id,
        "dataType": data_type,
        "energy": energy_type,
        "forecastMonth": month_key,
        "forecast": forecast_data,
        "lastUpdated": firestore.SERVER_TIMESTAMP
    })
    print(f"Successfully saved forecast for {prediction_doc_id} to Firestore.")

# --- MAIN EXECUTION BLOCK ---
if __name__ == '__main__':
    MACHINES_TO_PROCESS = fetch_all_machine_ids()

    if not MACHINES_TO_PROCESS:
        print("No machines found to process. Exiting script.")
        exit()

    DATA_TYPES_TO_PROCESS = ["output", "flatness", "inline", "crossline"]
    ENERGY_TYPES_TO_PROCESS = [
        "6X", "10X", "15X", "6X FFF", "10X FFF", "6E", 
        "9E", "12E", "15E", "18E"
    ]

    for machine in MACHINES_TO_PROCESS:
        machine_id = machine["machineId"]
        center_id = machine["centerId"]
        
        delete_future_predictions(machine_id)
        
        service_events = fetch_service_events(center_id)

        for data_type in DATA_TYPES_TO_PROCESS:
            for energy in ENERGY_TYPES_TO_PROCESS:
                print(f"\n--- Processing: {center_id} / {machine_id} / {data_type} / {energy} ---")
                
                all_data_df = fetch_all_historical_data(machine_id, data_type, energy)
                
                if all_data_df.empty:
                    print("No data found, skipping.")
                    continue

                full_forecast = train_and_predict(all_data_df, service_events)

                if full_forecast is not None:
                    last_historical_date = all_data_df['ds'].max()
                    future_predictions = full_forecast[full_forecast['ds'] >= last_historical_date]
                    future_predictions['month_key'] = future_predictions['ds'].dt.strftime('%Y-%m')
                    months_to_save = future_predictions['month_key'].unique()

                    for month in months_to_save:
                        monthly_forecast_chunk = future_predictions[future_predictions['month_key'] == month]
                        final_chunk_to_save = monthly_forecast_chunk.head(31)
                        save_monthly_prediction(machine_id, center_id, data_type, energy, month, final_chunk_to_save)
    
    print("\n\n--- All forecasts processed. Batch complete. ---")
