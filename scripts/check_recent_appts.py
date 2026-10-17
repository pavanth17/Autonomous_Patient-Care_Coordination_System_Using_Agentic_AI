import os
import sys

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import app, db
from models import AppointmentRequest, Patient
from datetime import datetime

with app.app_context():
    # Get last 5 appointments
    appts = AppointmentRequest.query.order_by(AppointmentRequest.requested_at.desc()).limit(5).all()
    print(f"Checking last {len(appts)} appointments:")
    for a in appts:
        patient = Patient.query.get(a.patient_id)
        if patient:
            print(f"ID: {a.id}, Patient: {patient.full_name}, Phone: {patient.phone}, Status: {a.status}, Source: {a.source}")
        else:
            print(f"ID: {a.id}, Patient: UNKNOWN, Status: {a.status}, Source: {a.source}")
