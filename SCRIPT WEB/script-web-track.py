# app.py - Main Flask application
from flask import Flask, render_template, request, jsonify, session, send_file
import pandas as pd
import os
import uuid
from werkzeug.utils import secure_filename
import tempfile
import json
from datetime import datetime
import io
import openpyxl
import logging
from logging.handlers import RotatingFileHandler
import traceback

# Create logs directory if it doesn't exist
log_dirs = [
    '..\\logs',
    '..\\logs\\access',
    '..\\logs\\error',
    '..\\logs\\activity'
]

for log_dir in log_dirs:
    os.makedirs(log_dir, exist_ok=True)

# Configure logging
# Access logger - tracks all requests
access_logger = logging.getLogger('access_logger')
access_logger.setLevel(logging.INFO)
access_file_handler = RotatingFileHandler(
    '..\\logs\\access\\access.log',
    maxBytes=10485760,  # 10MB
    backupCount=10
)
access_file_handler.setFormatter(logging.Formatter(
    '%(asctime)s - %(levelname)s - %(message)s'
))
access_logger.addHandler(access_file_handler)

# Error logger - tracks all errors
error_logger = logging.getLogger('error_logger')
error_logger.setLevel(logging.ERROR)
error_file_handler = RotatingFileHandler(
    '..\\logs\\error\\error.log',
    maxBytes=10485760,  # 10MB
    backupCount=10
)
error_file_handler.setFormatter(logging.Formatter(
    '%(asctime)s - %(levelname)s - %(message)s - %(pathname)s:%(lineno)d'
))
error_logger.addHandler(error_file_handler)

# Activity logger - tracks user actions
activity_logger = logging.getLogger('activity_logger')
activity_logger.setLevel(logging.INFO)
activity_file_handler = RotatingFileHandler(
    '..\\logs\\activity\\activity.log',
    maxBytes=10485760,  # 10MB
    backupCount=10
)
activity_file_handler.setFormatter(logging.Formatter(
    '%(asctime)s - %(levelname)s - %(message)s'
))
activity_logger.addHandler(activity_file_handler)

app = Flask(__name__, template_folder='..\\templates')
app.secret_key = 'your-secret-key-change-this'  # Change this in production
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max file size

# Global storage for workbook data (in production, use Redis or database)
workbook_storage = {}

# Request logging middleware
@app.before_request
def log_request():
    access_logger.info(f"Request: {request.method} {request.path} - IP: {request.remote_addr}")

@app.after_request
def log_response(response):
    access_logger.info(f"Response: {response.status_code} - {response.content_length} bytes")
    return response

@app.errorhandler(Exception)
def handle_exception(e):
    error_logger.error(f"Unhandled exception: {str(e)}\n{traceback.format_exc()}")
    return jsonify({'error': 'An unexpected error occurred'}), 500

def find_in_all_sheets(session_id, search_value):
    """Find search value in all sheets for a specific session"""
    if session_id not in workbook_storage:
        return "No data loaded"
    
    workbook_data = workbook_storage[session_id]['data']
    found_sheets = []
    search_str = str(search_value)
    
    for sheet_name, column_data in workbook_data.items():
        str_data = [str(val) for val in column_data if str(val) != 'nan']
        if search_str in str_data:
            found_sheets.append(sheet_name)
    
    if found_sheets:
        return ", ".join(found_sheets)
    else:
        return "Not Found"

@app.route('/')
def index():
    """Main page"""
    if 'session_id' not in session:
        session['session_id'] = str(uuid.uuid4())
        activity_logger.info(f"New session created: {session['session_id']}")
    return render_template('home.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    """Handle Excel file upload"""
    if 'file' not in request.files:
        activity_logger.warning(f"Upload attempt with no file - Session: {session.get('session_id')}")
        return jsonify({'error': 'No file uploaded'}), 400
    
    file = request.files['file']
    if file.filename == '':
        activity_logger.warning(f"Upload attempt with empty filename - Session: {session.get('session_id')}")
        return jsonify({'error': 'No file selected'}), 400
    
    if not file.filename.lower().endswith(('.xlsx', '.xls')):
        activity_logger.warning(f"Upload attempt with invalid file type: {file.filename} - Session: {session.get('session_id')}")
        return jsonify({'error': 'Please upload an Excel file (.xlsx or .xls)'}), 400
    
    try:
        # Read Excel file
        workbook_data = {}
        with pd.ExcelFile(file) as xls:
            for sheet_name in xls.sheet_names:
                df = pd.read_excel(xls, sheet_name=sheet_name)
                if len(df.columns) >= 4:  # Check if column D exists
                    column_d = df.iloc[:, 3].astype(str).dropna()  # Column D (index 3)
                    workbook_data[sheet_name] = column_d.tolist()
                else:
                    workbook_data[sheet_name] = []
        
        # Store in session storage
        session_id = session['session_id']
        workbook_storage[session_id] = {
            'data': workbook_data,
            'filename': secure_filename(file.filename),
            'upload_time': datetime.now().isoformat()
        }
        
        activity_logger.info(f"File uploaded: {secure_filename(file.filename)} with {len(workbook_data)} sheets - Session: {session_id}")
        
        return jsonify({
            'success': True,
            'message': f'Loaded data from {len(workbook_data)} sheets',
            'sheets': list(workbook_data.keys())
        })
        
    except Exception as e:
        error_logger.error(f"File upload error: {str(e)} - Session: {session.get('session_id')}")
        return jsonify({'error': f'Failed to load Excel file: {str(e)}'}), 500

@app.route('/add_csid', methods=['POST'])
def add_csid():
    """Add single CSID"""
    data = request.get_json()
    csid = data.get('csid', '').strip()
    
    if not csid:
        activity_logger.warning(f"Empty CSID submission - Session: {session.get('session_id')}")
        return jsonify({'error': 'CSID cannot be empty'}), 400
    
    session_id = session['session_id']
    found_sheets = find_in_all_sheets(session_id, csid)
    
    activity_logger.info(f"CSID search: {csid}, Found in: {found_sheets} - Session: {session_id}")
    
    return jsonify({
        'success': True,
        'csid': csid,
        'found_sheets': found_sheets
    })

@app.route('/add_bulk_csids', methods=['POST'])
def add_bulk_csids():
    """Add multiple CSIDs"""
    data = request.get_json()
    csids_text = data.get('csids', '').strip()
    
    if not csids_text:
        return jsonify({'error': 'No CSIDs provided'}), 400
    
    # Parse CSIDs
    import re
    csids = re.split(r'[\s,;\n\t]+', csids_text)
    csids = [csid.strip() for csid in csids if csid.strip()]
    
    if not csids:
        return jsonify({'error': 'No valid CSIDs found'}), 400
    
    session_id = session['session_id']
    results = []
    
    for csid in csids:
        found_sheets = find_in_all_sheets(session_id, csid)
        results.append({
            'csid': csid,
            'found_sheets': found_sheets
        })
    
    return jsonify({
        'success': True,
        'results': results,
        'count': len(results)
    })

@app.route('/refresh_csids', methods=['POST'])
def refresh_csids():
    """Refresh existing CSIDs"""
    data = request.get_json()
    csids = data.get('csids', [])
    
    if not csids:
        return jsonify({'error': 'No CSIDs to refresh'}), 400
    
    session_id = session['session_id']
    results = []
    
    for csid in csids:
        found_sheets = find_in_all_sheets(session_id, csid)
        results.append({
            'csid': csid,
            'found_sheets': found_sheets
        })
    
    return jsonify({
        'success': True,
        'results': results
    })

@app.route('/export', methods=['POST'])
def export_data():
    """Export data to Excel"""
    data = request.get_json()
    export_data = data.get('data', [])
    
    if not export_data:
        activity_logger.warning(f"Export attempt with no data - Session: {session.get('session_id')}")
        return jsonify({'error': 'No data to export'}), 400
    
    try:
        # Create DataFrame
        df = pd.DataFrame(export_data)
        
        # Create Excel file in memory
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='CSID_Results')
        
        output.seek(0)
        
        filename = f'csid_results_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
        activity_logger.info(f"Data exported to {filename} with {len(export_data)} records - Session: {session.get('session_id')}")
        
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=filename
        )
        
    except Exception as e:
        error_logger.error(f"Export error: {str(e)} - Session: {session.get('session_id')}")
        return jsonify({'error': f'Export failed: {str(e)}'}), 500

@app.route('/reset_file', methods=['POST'])
def reset_file():
    """Reset the uploaded file data"""
    session_id = session.get('session_id')
    if session_id in workbook_storage:
        filename = workbook_storage[session_id].get('filename', 'unknown')
        del workbook_storage[session_id]
        activity_logger.info(f"File data reset: {filename} - Session: {session_id}")
    return jsonify({'success': True, 'message': 'File data reset successfully'})

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5050)