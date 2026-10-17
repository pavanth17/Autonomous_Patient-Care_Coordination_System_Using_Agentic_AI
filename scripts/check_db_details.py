from app import app
from models import Patient, StaffMember

with app.app_context():
    print("--- Patients ---")
    patients = Patient.query.all()
    for p in patients:
        print(f"ID: {p.id}, Name: {p.full_name}, Bed: {p.bed_number}")
    
    print("\n--- On-Duty Staff ---")
    staff = StaffMember.query.filter_by(is_on_duty=True).all()
    for s in staff:
        print(f"ID: {s.id}, Name: {s.full_name}, Role: {s.role}, Phone: {s.phone}")
