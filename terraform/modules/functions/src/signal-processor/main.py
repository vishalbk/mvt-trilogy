import json
import base64
import functions_framework
from google.cloud import firestore
from datetime import datetime
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

db = firestore.Client()


@functions_framework.cloud_event
def process_signal(cloud_event):
    """
    Processes incoming signals from AWS EventBridge relay.
    Writes to Firestore collection structure: dashboard_signals/{dashboard}/{signal_id}
    Maintains a 'latest' document for each dashboard panel.
    """
    try:
        # Decode Pub/Sub message
        pubsub_message = base64.b64decode(cloud_event.data["message"]["data"]).decode()
        signal_data = json.loads(pubsub_message)

        logger.info(f"Processing signal: {signal_data.get('signal_id', 'unknown')}")

        # Extract signal metadata
        dashboard_id = signal_data.get('dashboard_id', 'default')
        signal_id = signal_data.get('signal_id')
        event_type = signal_data.get('event_type')
        severity = signal_data.get('severity', 0.0)

        if not signal_id:
            return {'status': 'error', 'message': 'Missing signal_id'}, 400

        # Add processing timestamp
        signal_data['processed_at'] = datetime.utcnow()

        # Write to specific signal document
        signal_ref = (
            db.collection('dashboard_signals')
            .document(dashboard_id)
            .collection('signals')
            .document(signal_id)
        )
        signal_ref.set(signal_data, merge=True)

        # Update 'latest' document for this dashboard
        latest_ref = (
            db.collection('dashboard_signals')
            .document(dashboard_id)
            .collection('signals')
            .document('_latest')
        )
        latest_ref.set({
            'last_signal_id': signal_id,
            'event_type': event_type,
            'severity': severity,
            'updated_at': datetime.utcnow(),
            'dashboard_id': dashboard_id
        }, merge=True)

        logger.info(f"Successfully processed signal {signal_id} for dashboard {dashboard_id}")
        return {'status': 'success', 'signal_id': signal_id}, 200

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse JSON: {str(e)}")
        return {'status': 'error', 'message': 'Invalid JSON'}, 400
    except Exception as e:
        logger.error(f"Error processing signal: {str(e)}")
        return {'status': 'error', 'message': str(e)}, 500
