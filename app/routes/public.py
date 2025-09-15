from flask import Blueprint, jsonify, request
from app.services.firebase import db
import logging

# All routes in this file will be prefixed with /public
bp = Blueprint('public', __name__, url_prefix='/public')

@bp.route('/groups', methods=['GET'])
def get_public_groups():
    """Fetches a unique list of all institution groups."""
    try:
        institutions_ref = db.collection('institutions').stream()
        # Use a set to automatically handle uniqueness of group IDs
        groups = {doc.to_dict().get('parentGroup') for doc in institutions_ref if doc.to_dict().get('parentGroup')}
        return jsonify(sorted(list(groups))), 200
    except Exception as e:
        logging.error(f"Error fetching public groups: {str(e)}", exc_info=True)
        return jsonify({'message': 'Could not retrieve organization list.'}), 500

@bp.route('/institutions-by-group', methods=['GET'])
def get_public_institutions_by_group():
    """Fetches a list of institutions belonging to a specific group."""
    group_id = request.args.get('group')
    if not group_id:
        return jsonify({'message': 'Group ID is required.'}), 400
    try:
        institutions_ref = db.collection('institutions').where('parentGroup', '==', group_id).stream()
        institutions = [{'name': doc.to_dict().get('name'), 'centerId': doc.to_dict().get('centerId')} for doc in institutions_ref]
        # Sort the list in Python after fetching 
        institutions.sort(key=lambda x: x.get('name', ''))
        return jsonify(institutions), 200
    except Exception as e:
        logging.error(f"Error fetching institutions for group {group_id}: {str(e)}", exc_info=True)
        return jsonify({'message': 'Could not retrieve institution list.'}), 500

@bp.route('/all-institutions', methods=['GET'])
def get_all_institutions():
    """Fetches a list of all institutions."""
    try:
        institutions_ref = db.collection('institutions').stream()
        institutions = [{'name': doc.to_dict().get('name'), 'centerId': doc.to_dict().get('centerId')} for doc in institutions_ref]
        institutions.sort(key=lambda x: x.get('name', ''))
        return jsonify(institutions), 200
    except Exception as e:
        logging.error(f"Error fetching all institutions: {str(e)}", exc_info=True)
        return jsonify({'message': 'Could not retrieve institution list.'}), 500
