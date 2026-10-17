
from app import app
from models import StaffMember, Shift
from datetime import datetime
from sqlalchemy import func

with app.app_context():
    print("Listing ALL specialized doctors:")
    docs = StaffMember.query.filter_by(role='doctor').all()
    for d in docs:
        print(f"ID: {d.id} | Name: {d.full_name} | Dept: {d.department} | Spec: {d.specialization}")
    
    print("\nListing shifts for today:")
    today = datetime.now().date()
    shifts = Shift.query.filter(func.date(Shift.start_time) == today).all()
    for s in shifts:
        print(f"Shift ID: {s.id} | Staff ID: {s.staff_id} | Start: {s.start_time}")
