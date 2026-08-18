"""
Big Players Strategy Routes and API Handlers
"""
import os
import json
import logging
from flask import Blueprint, request, jsonify, render_template

logger = logging.getLogger(__name__)

bp = Blueprint('bigplayers', __name__, url_prefix='/api/bigplayers')

@bp.route('/screen', methods=['GET', 'POST'])
def screen():
    """Run Big Players screener"""
    budget = float(request.args.get('budget', 100000))
    parts = int(request.args.get('parts', 5))
    try:
        from strategy.bigplayers.strategy import run_big_players_screener
        results = run_big_players_screener(budget=budget, parts=parts)
        return jsonify({"success": True, "data": results})
    except Exception as e:
        logger.error(f"Error in Big Players screener: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/calculate-qty', methods=['POST'])
def calculate_qty():
    data = request.get_json() or {}
    budget = float(data.get('budget', 100000))
    parts = int(data.get('parts', 5))
    symbols = data.get('symbols', [])
    try:
        from strategy.bigplayers.strategy import compute_quantities
        res = compute_quantities(symbols, budget, parts)
        return jsonify({"success": True, "data": res})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/status', methods=['GET'])
def status():
    return jsonify({
        "status": "active",
        "strategy": "Big Players Strategy",
        "features": ["Support Bounce", "Volume Surge", "Institutional Footprint"]
    })
