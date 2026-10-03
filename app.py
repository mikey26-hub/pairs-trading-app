import os
import time
import threading
import logging
import pandas as pd
import numpy as np
import yfinance as yf
import statsmodels.api as sm
from flask import Flask, jsonify, request, render_template_string
import alpaca_trade_api as tradeapi

app = Flask(__name__)
logging.getLogger('werkzeug').setLevel(logging.ERROR)

# Alpaca API Setup (using environment variables)
ALPACA_KEY = os.environ.get('ALPACA_API_KEY', '')
ALPACA_SECRET = os.environ.get('ALPACA_SECRET_KEY', '')
ALPACA_BASE_URL = 'https://paper-api.alpaca.markets'  # Change to live URL for real money

api = None
if ALPACA_KEY and ALPACA_SECRET:
    try:
        api = tradeapi.REST(ALPACA_KEY, ALPACA_SECRET, ALPACA_BASE_URL, api_version='v2')
    except Exception as e:
        print(f"Alpaca connection error: {e}")

state = {
    "capital": 100000.00,
    "kill_switch_active": False,
    "position": "FLAT"  # "FLAT", "LONG_SPREAD", or "SHORT_SPREAD"
}

cache = {
    "last_fetch": 0,
    "z_score": "0.124",
    "xom": "118.50",
    "cvx": "152.30",
    "status": "OK",
    "mode": "LIVE"
}

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Pairs Trading System</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #121824; color: white; padding: 15px; margin: 0; }
        .card { background: #1E2638; padding: 15px; border-radius: 10px; margin-bottom: 15px; border: 1px solid #2A3447; }
        .value { font-size: 28px; font-weight: bold; color: #2979FF; margin-top: 5px; }
        .btn { width: 100%; padding: 16px; font-size: 16px; font-weight: bold; background: #FF5252; color: white; border: none; border-radius: 8px; cursor: pointer; }
        .btn:active { transform: scale(0.98); }
        .ctrl-btn { flex: 1; padding: 10px; border: none; font-weight: bold; border-radius: 6px; cursor: pointer; font-size: 13px; }
        .ctrl-btn:active { opacity: 0.8; }
        .status { padding: 12px; border-radius: 6px; font-weight: bold; text-align: center; margin-bottom: 15px; font-size: 14px; letter-spacing: 0.5px; }
    </style>
</head>
<body>
    <h2 style="text-align:center; font-size: 20px; margin-bottom: 20px;">Candidate B Control Center</h2>
    
    <div id="status" class="status" style="background:#00E676; color:black;">SYSTEM ONLINE & ARMED</div>
    
    <div class="card">
        <div style="font-size:12px; color:#8A99AD; font-weight: bold; text-transform: uppercase;">Z-Score (Mean Reversion)</div>
        <div id="zscore" class="value">Loading...</div>
    </div>
    
    <div class="card">
        <div style="font-size:12px; color:#8A99AD; font-weight: bold; text-transform: uppercase;">Live Pair Quotes</div>
        <div id="prices" style="font-size:18px; font-weight:bold; margin-top:5px; color:#FFFFFF;">Loading...</div>
    </div>
    
    <div class="card">
        <div style="font-size:12px; color:#8A99AD; font-weight: bold; text-transform: uppercase;">Broker Position State</div>
        <div id="position" class="value" style="color:#00E676;">FLAT</div>
    </div>

    <!-- SYNTHETIC RESPONSE TEST PANEL -->
    <div class="card" style="border-color: #FFD600;">
        <div style="font-size:12px; color:#FFD600; font-weight: bold; text-transform: uppercase; margin-bottom: 8px;">Force Synthetic Response & Orders</div>
        <div style="display: flex; gap: 8px;">
            <button class="ctrl-btn" onclick="forceState(2.50, 122.50, 148.10)" style="background:#FF9800; color:black;">Force High (+2.5)</button>
            <button class="ctrl-btn" onclick="forceState(-2.20, 114.00, 156.30)" style="background:#2979FF; color:white;">Force Low (-2.2)</button>
            <button class="ctrl-btn" onclick="resetLive()" style="background:#4A5568; color:white;">Reset Live</button>
        </div>
    </div>

    <button class="btn" onclick="toggleKill()">TOGGLE EMERGENCY KILL SWITCH</button>

    <script>
        function update() {
            fetch('/data')
                .then(res => res.json())
                .then(d => {
                    document.getElementById('zscore').innerText = d.z_score;
                    document.getElementById('prices').innerText = 'XOM: $' + d.xom + '  |  CVX: $' + d.cvx;
                    document.getElementById('position').innerText = d.position;
                    let st = document.getElementById('status');
                    if(d.kill) {
                        st.innerText = 'HALTED / KILL SWITCH ACTIVE';
                        st.style.background = '#FF5252';
                        st.style.color = 'white';
                    } else {
                        st.innerText = 'SYSTEM ONLINE & ARMED';
                        st.style.background = '#00E676';
                        st.style.color = 'black';
                    }
                })
                .catch(err => {
                    let st = document.getElementById('status');
                    st.innerText = 'DISCONNECTED FROM BACKEND';
                    st.style.background = '#FF9800';
                    st.style.color = 'black';
                });
        }

        function toggleKill() {
            fetch('/toggle', {method:'POST'}).then(() => update());
        }

        function forceState(z, xom, cvx) {
            fetch('/force_response', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ z_score: z, xom: xom, cvx: cvx })
            }).then(() => update());
        }

        function resetLive() {
            fetch('/force_response', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ reset: true })
            }).then(() => update());
        }

        setInterval(update, 3000);
        update();
    </script>
</body>
</html>
"""

def execute_paired_trades(z_val):
    """Submits market orders to Alpaca when thresholds are crossed."""
    global api
    if not api or state["kill_switch_active"]:
        return

    try:
        # Sell Spread Condition: Z > 2.0 (Short XOM, Long CVX)
        if z_val > 2.0 and state["position"] == "FLAT":
            api.submit_order(symbol='XOM', qty=10, side='sell', type='market', time_in_force='gtc')
            api.submit_order(symbol='CVX', qty=8, side='buy', type='market', time_in_force='gtc')
            state["position"] = "SHORT_SPREAD (SHORT XOM / LONG CVX)"

        # Buy Spread Condition: Z < -2.0 (Long XOM, Short CVX)
        elif z_val < -2.0 and state["position"] == "FLAT":
            api.submit_order(symbol='XOM', qty=10, side='buy', type='market', time_in_force='gtc')
            api.submit_order(symbol='CVX', qty=8, side='sell', type='market', time_in_force='gtc')
            state["position"] = "LONG_SPREAD (LONG XOM / SHORT CVX)"

        # Mean Reversion Close Condition: |Z| < 0.2
        elif abs(z_val) < 0.2 and state["position"] != "FLAT":
            api.close_all_positions()
            state["position"] = "FLAT"
    except Exception as e:
        print(f"Execution Error: {e}")

def fetch_live_data():
    try:
        data = yf.download(tickers="XOM CVX", period="1mo", interval="1d", progress=False)
        closes = data['Close'] if 'Close' in data else data
        df = closes[['XOM', 'CVX']].dropna()

        if not df.empty:
            X = sm.add_constant(df['CVX'])
            model = sm.OLS(df['XOM'], X).fit()
            beta = model.params['CVX']
            alpha = model.params['const']
            
            spread = df['XOM'] - (beta * df['CVX']) - alpha
            z = (spread - spread.mean()) / spread.std()
            z_val = float(z.iloc[-1])
            
            cache["z_score"] = f"{z_val:.3f}"
            cache["xom"] = f"{float(df['XOM'].iloc[-1]):.2f}"
            cache["cvx"] = f"{float(df['CVX'].iloc[-1]):.2f}"
            cache["status"] = "OK"
            cache["last_fetch"] = time.time()
            
            execute_paired_trades(z_val)
    except Exception as e:
        pass

def update_market_cache():
    while True:
        if cache.get("mode") == "LIVE" and (time.time() - cache["last_fetch"] >= 60):
            fetch_live_data()
        time.sleep(2)

threading.Thread(target=update_market_cache, daemon=True).start()

@app.route('/')
def home():
    return render_template_string(HTML_TEMPLATE)

@app.route('/data')
def get_data():
    return jsonify({
        'z_score': cache['z_score'],
        'xom': cache['xom'],
        'cvx': cache['cvx'],
        'position': state['position'],
        'kill': state['kill_switch_active']
    })

@app.route('/toggle', methods=['POST'])
def toggle():
    state['kill_switch_active'] = not state['kill_switch_active']
    return jsonify({'status': 'ok', 'kill_switch_active': state['kill_switch_active']})

@app.route('/force_response', methods=['POST'])
def force_response():
    data = request.get_json() or {}
    if data.get('reset'):
        cache['mode'] = 'LIVE'
        fetch_live_data()
    else:
        cache['mode'] = 'FORCED'
        if 'z_score' in data: 
            cache['z_score'] = str(data['z_score'])
            execute_paired_trades(float(data['z_score']))
        if 'xom' in data: cache['xom'] = str(data['xom'])
        if 'cvx' in data: cache['cvx'] = str(data['cvx'])
        
    return jsonify({'status': 'ok', 'cache': cache})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
