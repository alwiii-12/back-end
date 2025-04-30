from flask import Flask, request, jsonify
from flask_cors import CORS
import sqlite3

app = Flask(__name__)
CORS(app)

# Initialize SQLite DB
def init_db():
    conn = sqlite3.connect('linac_data.db')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS monthly_data (
            month TEXT PRIMARY KEY,
            data TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

@app.route('/')
def home():
    return "LINAC QA backend is running."

@app.route('/save_month_data', methods=['POST'])
def save_month_data():
    content = request.get_json()
    month = content['month']
    data = str(content['data'])

    conn = sqlite3.connect('linac_data.db')
    c = conn.cursor()
    c.execute('REPLACE INTO monthly_data (month, data) VALUES (?, ?)', (month, data))
    conn.commit()
    conn.close()
    return jsonify({'status': 'success', 'message': 'Data saved'})

@app.route('/load_month_data', methods=['GET'])
def load_month_data():
    month = request.args.get('month')

    conn = sqlite3.connect('linac_data.db')
    c = conn.cursor()
    c.execute('SELECT data FROM monthly_data WHERE month = ?', (month,))
    row = c.fetchone()
    conn.close()

    if row:
        import ast
        return jsonify({'data': ast.literal_eval(row[0])})
    else:
        return jsonify({'data': None})

if __name__ == '__main__':
    app.run(debug=True)
