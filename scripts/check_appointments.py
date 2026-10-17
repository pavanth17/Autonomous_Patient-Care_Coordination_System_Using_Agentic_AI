import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import app, db
from models import AppointmentRequest, Patient
import logging

with app.app_context():
    appts = AppointmentRequest.query.order_by(AppointmentRequest.id.desc()).limit(10).all()
    print("Recent Appointments:")
    for a in appts:
        patient = Patient.query.get(a.patient_id)
        p_name = f"{patient.first_name} {patient.last_name}" if patient else "Unknown"
        print(f"ID: {a.id} | Status: {a.status} | Patient: {p_name} | Created: {a.requested_at}")
