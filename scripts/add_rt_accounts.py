import os
import sys

# Add the current directory to the search path for imports
sys.path.append(os.getcwd())

from app import app, db
from models import StaffMember

def create_rt_accounts():
    with app.app_context():
        # Account for DOC_RT
        doc_rt = StaffMember.query.filter_by(staff_id="DOC_RT").first()
        if not doc_rt:
            doc_rt = StaffMember(
                staff_id="DOC_RT",
                first_name="RealTime",
                last_name="Doctor",
                email="doc_rt@caresync.hospital",
                phone="555-000-0000",
                role="doctor",
                department="Emergency",
                is_on_duty=True,
                is_active=True
            )
            doc_rt.set_password("password123")
            db.session.add(doc_rt)
            print("Successfully created DOC_RT (Doctor Real-Time) account.")
        else:
            print("DOC_RT account already exists.")

        # Account for NRS_RT
        nrs_rt = StaffMember.query.filter_by(staff_id="NRS_RT").first()
        if not nrs_rt:
            nrs_rt = StaffMember(
                staff_id="NRS_RT",
                first_name="RealTime",
                last_name="Nurse",
                email="nrs_rt@caresync.hospital",
                phone="555-000-1111",
                role="nurse",
                department="Emergency",
                is_on_duty=True,
                is_active=True
            )
            nrs_rt.set_password("password123")
            db.session.add(nrs_rt)
            print("Successfully created NRS_RT (Nurse Real-Time) account.")
        else:
            print("NRS_RT account already exists.")

        # Commit changes to database
        db.session.commit()
        print("Database updated successfully.")

if __name__ == "__main__":
    create_rt_accounts()
