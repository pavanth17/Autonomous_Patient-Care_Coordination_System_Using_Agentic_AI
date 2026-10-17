import os
import logging
from twilio.rest import Client

logger = logging.getLogger(__name__)

def format_to_e164(phone: str, default_prefix: str = "+91") -> str:
    """Format a phone number to E.164, default to India +91 if prefix missing"""
    if not phone: return ""
    # Remove all non-digit characters except '+'
    cleaned = "".join(c for c in phone if c.isdigit() or c == '+')
    if not cleaned: return ""
    if cleaned.startswith('+'):
        return cleaned
    # Remove leading zero if present
    if cleaned.startswith('0'):
        cleaned = cleaned[1:]
    return f"{default_prefix}{cleaned}"

def send_vitals_alert_sms(patient_name, alert_title, alert_message, to_number=None):
    """
    Sends an SMS alert using Twilio.
    
    Args:
        patient_name (str): Name of the patient
        alert_title (str): Title of the alert
        alert_message (str): Message content
        to_number (str, optional): Destination phone number. 
                                  Defaults to +919515104279.
    """
    account_sid = os.environ.get('TWILIO_ACCOUNT_SID')
    auth_token = os.environ.get('TWILIO_AUTH_TOKEN')
    from_number = os.environ.get('TWILIO_PHONE_NUMBER')
    
    # Default to the user's number if not provided
    if not to_number:
        to_number = "+919515104279" # Default number from test scripts
    
    # NEW: Format to E.164 for Twilio
    formatted_to = format_to_e164(to_number)

    logger.info(f"Twilio Config - SID: {account_sid[:5] if account_sid else 'NONE'}***, Token: {auth_token[:5] if auth_token else 'NONE'}***, From: {from_number}")
    
    if not all([account_sid, auth_token, from_number]):
        logger.error(f"Twilio credentials missing! SID: {bool(account_sid)}, Token: {bool(auth_token)}, From: {bool(from_number)}")
        return False

    try:
        client = Client(account_sid, auth_token)
        message_body = f"CareSync AI ALERT: {alert_title}\n{alert_message}"
        
        logger.info(f"Sending Twilio SMS to {formatted_to}: {alert_title}")
        
        message = client.messages.create(
            body=message_body,
            from_=from_number,
            to=formatted_to
        )
        
        logger.info(f"SMS sent successfully. SID: {message.sid}")
        return True
    except Exception as e:
        logger.error(f"Failed to send Twilio SMS: {e}")
        return False
