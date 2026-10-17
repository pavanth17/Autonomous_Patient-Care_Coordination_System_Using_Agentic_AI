import sys
import os
from datetime import datetime, timedelta
import logging

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import app, db
from models import Patient, AppointmentRequest, StaffMember
from ai_appointment_router import appointment_router, notification_service

def debug_booking():
    with app.app_context():
        # Setup logger to see output
        logger = logging.getLogger()
        logger.setLevel(logging.INFO)
        handler = logging.StreamHandler(sys.stdout)
        logger.addHandler(handler)
        
        print("\n--- Debugging Appointment Booking ---")
        
        # 1. Ensure we have a patient
        patient = Patient.query.filter_by(patient_id='SMS_TEST_002').first()
        if not patient:
            print("Creating test patient...")
            patient = Patient(
                patient_id='SMS_TEST_002',
                first_name='FixTester',
                last_name='User',
                date_of_birth=datetime(1995, 1, 1).date(),
                gender='Female',
                phone='9876543210'
            )
            db.session.add(patient)
            db.session.commit()
        
        patient_id = patient.id
        
        # 2. Simulate patient_book_appointment logic
        print(f"Booking appointment for patient {patient_id}...")
        
        pref_date = datetime.now() + timedelta(days=2)
        
        appt = AppointmentRequest(
            patient_id=patient_id,
            preferred_date=pref_date,
            preferred_time='11:00 AM',
            notes='Debug booking',
            appointment_type='routine',
            department='Cardiology',
            status='pending'
        )
        db.session.add(appt)
        db.session.commit()
        print(f"Appointment {appt.id} created and committed with status 'pending'")
        
        # 3. Send booking notification
        print("Sending booking notification...")
        # Mock _send_sms to see output
        notification_service._send_sms = lambda ph, msg: print(f"[MOCK SMS] To: {ph}\nMessage:\n{msg}") or True
        notification_service.send_booking_notification(appt)
        
        # 4. Try routing
        print("Attempting AI routing...")
        allocated_doctor = None
        try:
            allocated_doctor = appointment_router.route_appointment(appt)
            if allocated_doctor:
                print(f"Allocated to Dr. {allocated_doctor.first_name} {allocated_doctor.last_name}")
                print(f"Appointment status now: {appt.status}")
            else:
                print("No doctor allocated.")
        except Exception as e:
            print(f"Routing failed with error: {e}")
        
        # 5. Send confirmation notification
        print("Sending confirmation notification...")
        notification_service.send_appointment_confirmation(appt, allocated_doctor)
        
        print("\nDebug complete.")

if __name__ == "__main__":
    debug_booking()
