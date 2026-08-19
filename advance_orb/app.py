"""
Advance ORB Application Server (Flask/FastAPI compatible)
"""
import os
import json
import logging
from flask import Flask, request, jsonify, render_template

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@app.route('/health')
def health():
    return jsonify({"status": "healthy", "service": "Advance ORB Python Engine"})

@app.route('/api/strategies/advanceorb', methods=['GET'])
def get_advance_orb():
    budget = float(request.args.get('budget', 100000))
    parts = int(request.args.get('parts', 5))
    above_ema = request.args.get('above_ema', 'true').lower() == 'true'
    inside915 = request.args.get('inside915', 'false').lower() == 'true'

    try:
        from advance_orb.common import get_advance_orb_screened_data
        data = get_advance_orb_screened_data(budget, parts, above_ema, inside915)
        return jsonify(data)
    except Exception as e:
        logger.error(f"Error fetching ORB data: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
