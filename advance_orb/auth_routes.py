import os
import logging
from flask import Blueprint, request, jsonify, session

bp = Blueprint('auth', __name__, url_prefix='/auth')
logger = logging.getLogger(__name__)

@bp.route('/me', methods=['GET'])
def me():
    user = session.get('user', {
        'id': 'usr_smarttrader_01',
        'email': 'trader@tradealgopro.com',
        'name': 'Active Trader'
    })
    return jsonify({'ok': True, 'user': user})

@bp.route('/signin', methods=['POST'])
def signin():
    data = request.get_json() or {}
    email = data.get('email', '')
    password = data.get('password', '')
    if not email:
        return jsonify({'ok': False, 'detail': 'Email required'}), 400
    user = {'id': 'usr_01', 'email': email, 'name': email.split('@')[0]}
    session['user'] = user
    return jsonify({'ok': True, 'user': user, 'access_token': 'mock_token', 'token_type': 'bearer'})

@bp.route('/signup', methods=['POST'])
def signup():
    return signin()

@bp.route('/logout', methods=['POST'])
def logout():
    session.pop('user', None)
    return jsonify({'ok': True, 'message': 'Logged out successfully'})
