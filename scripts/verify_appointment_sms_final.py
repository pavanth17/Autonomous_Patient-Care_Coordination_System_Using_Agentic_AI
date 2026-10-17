import sys
import os
from datetime import datetime, timedelta
import logging

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import app, db
from models import Patient, AppointmentRequest, StaffMember
from appointment_routing import notification_service, appointment_router

logging.basicConfig(level=logging.INFO)

def verify_appointment_sms():
    with app.app_context():
        print("\n--- Verifying Appointment Booking SMS ---")
        
        target_phone = "+919700443157"
        email = "sms_test_user@example.com"
        
        # 1. Setup Patient
        patient = Patient.query.filter_by(phone=target_phone).first()
        if not patient:
            patient = Patient.query.filter_by(email=email).first()
            
        if patient:
            patient.phone = target_phone
            patient.first_name = "SMS-Test"
            patient.last_name = "User"
            db.session.commit()
            print(f"Using existing patient: {patient.patient_id} ({patient.full_name})")
        else:
            patient = Patient(
                patient_id='PAT_SMS_TEST_01',
                first_name='SMS-Test',
                last_name='User',
                email=email,
                phone=target_phone,
                date_of_birth=datetime(1990, 1, 1).date(),
                gender='Male',
                status='outpatient'
            )
            db.session.add(patient)
            db.session.commit()
            print(f"Created new patient: {patient.patient_id}")

        # 2. Create Appointment Request (Simulating public booking)
        appt = AppointmentRequest(
            patient_id=patient.id,
            preferred_date=datetime.now() + timedelta(days=2),
            preferred_time="10:00 AM",
            notes="Testing SMS notification on booking.",
            status='pending',
            source='landing_page',
            appointment_type='consultation',
            department='General Medicine'
        )
        db.session.add(appt)
        db.session.commit()
        print(f"Created appointment request: {appt.id}")

        # 3. Trigger Notifications (Mimicking routes.py logic)
        print(f"Triggering booking notification to {patient.phone}...")
        booking_sent = notification_service.send_booking_notification(appt)
        if booking_sent:
            print("[SUCCESS] Booking notification triggered.")
        else:
            print("[FAIL] Booking notification failed.")

        # 4. Allocate Doctor and Trigger Confirmation
        print(f"Allocating doctor via AI...")
        allocated_doctor = appointment_router.route_appointment(appt)
        if allocated_doctor:
            print(f"AI allocated doctor: Dr. {allocated_doctor.full_name}")
            print(f"Triggering confirmation notification to {patient.phone}...")
            confirm_sent = notification_service.send_appointment_confirmation(appt, allocated_doctor)
            if confirm_sent:
                print("[SUCCESS] Confirmation notification triggered.")
            else:
                print("[FAIL] Confirmation notification failed.")
        else:
            print("[WARN] No doctor allocated. Skipping confirmation notification.")

if __name__ == "__main__":
    verify_appointment_sms()
