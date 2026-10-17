from app import app, db
from models import StaffMember, Patient

with app.app_context():
    print("--- STAFF ---")
    staff = StaffMember.query.order_by(StaffMember.staff_id).all()
    for s in staff:
        print(f"ID: {s.staff_id} | Name: {s.full_name} | Role: {s.role} | Email: {s.email} | Active: {s.is_active} | OnDuty: {s.is_on_duty}")

    print("\n--- PATIENTS (First 10) ---")
    patients = Patient.query.order_by(Patient.patient_id).limit(10).all()
    for p in patients:
        print(f"ID: {p.patient_id} | Name: {p.full_name} | Phone: {p.phone} | Status: {p.status}")
