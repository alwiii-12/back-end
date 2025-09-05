from flask import Blueprint, request, jsonify
from firebase_admin import firestore
import logging
import pandas as pd
from prophet import Prophet
from calendar import monthrange
from datetime import timedelta

db = firestore.client()
logger = logging.getLogger(__name__)

forecasting_bp = Blueprint('forecasting_bp', __name__, url_prefix='/forecast')

@forecasting_bp.route('/predictions', methods=['GET'])
def get_predictions():
    try:
        uid, data_type, energy, month = request.args.get('uid'), request.args.get('dataType'), request.args.get('energy'), request.args.get('month')
        if not all([uid, data_type, energy, month]):
            return jsonify({'error': 'Missing required parameters'}), 400

        user_doc = db.collection("users").document(uid).get()
        if not user_doc.exists: return jsonify({'error': 'User not found'}), 404
        center_id = user_doc.to_dict().get("centerId")
        if not center_id: return jsonify({'error': 'User has no center'}), 400

        prediction_doc = db.collection("linac_predictions").document(f"{center_id}_{data_type}_{energy}_{month}").get()
        if prediction_doc.exists:
            return jsonify(prediction_doc.to_dict()), 200
        else:
            return jsonify({'error': f'Prediction not found'}), 404
    except Exception as e:
        logger.error(f"Get predictions error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500

@forecasting_bp.route('/historical', methods=['POST'])
def get_historical_forecast():
    try:
        content = request.get_json(force=True)
        uid, month_param, data_type, energy = content.get('uid'), content.get('month'), content.get('dataType'), content.get('energy')
        if not all([uid, month_param, data_type, energy]):
            return jsonify({'error': 'Missing parameters'}), 400

        user_doc = db.collection("users").document(uid).get()
        if not user_doc.exists: return jsonify({'error': 'User not found'}), 404
        center_id = user_doc.to_dict().get("centerId")

        # Fetch data for training
        months_ref = db.collection("linac_data").document(center_id).collection("months").stream()
        all_values = []
        end_date_for_training = pd.to_datetime(month_param) - timedelta(days=1)

        for month_doc in months_ref:
            month_id_str = month_doc.id.replace("Month_", "")
            if pd.to_datetime(month_id_str) > end_date_for_training: continue
            
            month_data = month_doc.to_dict()
            for row_data in month_data.get(f"data_{data_type}", []):
                if row_data.get("energy") == energy:
                    year, mon = map(int, month_id_str.split("-"))
                    for i, value in enumerate(row_data.get("values", [])):
                        try:
                            if value: all_values.append({"ds": pd.to_datetime(f"{year}-{mon}-{i+1}"), "y": float(value)})
                        except (ValueError, TypeError): continue
        
        if len(all_values) < 10: return jsonify({'error': 'Not enough historical data.'}), 404
        
        # Train and predict
        df_train = pd.DataFrame(all_values).drop_duplicates(subset='ds', keep='last')
        model = Prophet().fit(df_train)
        year, mon = map(int, month_param.split("-"))
        future = model.make_future_dataframe(periods=monthrange(year, mon)[1], freq='D')
        forecast_df = model.predict(future)
        
        # Filter for the requested month
        forecast_df = forecast_df[forecast_df['ds'].dt.strftime('%Y-%m') == month_param]

        return jsonify({
            'forecast': forecast_df[['ds', 'yhat', 'yhat_lower', 'yhat_upper']].to_dict('records')
        }), 200
    except Exception as e:
        logger.error(f"Historical forecast error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500
