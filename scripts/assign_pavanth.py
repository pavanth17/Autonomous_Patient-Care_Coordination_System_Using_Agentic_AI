import os
import sys

# Add the current directory to the search path for imports
sys.path.append(os.getcwd())

from app import app, db
from models import StaffMember, Patient

def assign_pavanth_to_rt():
    with app.app_context():
        # Find Pavanth Kumar
        patient = Patient.query.filter((Patient.first_name + ' ' + Patient.last_name).ilike('%pavanth kumar%')).first()
        if not patient:
            # Create him if he doesn't exist
            from datetime import datetime
            patient = Patient(
                patient_id="PAT_PAVANTH",
                first_name="Pavanth",
                last_name="Kumar",
                date_of_birth=datetime(1995, 1, 1),
                gender="Male",
                status="admitted",
                department="Emergency",
                room_number="RT-101",
                bed_number="1"
            )
            db.session.add(patient)
            db.session.commit()
            print("Created patient Pavanth Kumar.")
        
        # Find DOC_RT and NRS_RT
        doc_rt = StaffMember.query.filter_by(staff_id="DOC_RT").first()
        nrs_rt = StaffMember.query.filter_by(staff_id="NRS_RT").first()

        if not doc_rt or not nrs_rt:
            print("Real-time accounts not found. Please run scripts/add_rt_accounts.py first.")
            return

        # Assign Pavanth to these RT staff members
        patient.assigned_doctor_id = doc_rt.id
        patient.assigned_nurse_id = nrs_rt.id
        patient.status = "admitted" # Ensure he is visible
        
        db.session.commit()
        print(f"Patient {patient.full_name} (ID: {patient.id}) assigned to DOC_RT and NRS_RT.")

if __name__ == "__main__":
    assign_pavanth_to_rt()
