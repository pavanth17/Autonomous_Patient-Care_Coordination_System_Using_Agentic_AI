from app import app, db, socketio, generate_ai_response, groq_client
import routes  # Ensure routes are registered
import os
import sys
import argparse
import logging
import random
import threading
import time
from datetime import datetime, timedelta

# Import models
from models import StaffMember, Patient, VitalSign, Alert, RiskAssessment, ChatMessage, Medication, LabReport, Ward, AppointmentRequest

# Import utilities
from synthetic_data import initialize_synthetic_data
try:
    from predictive_analytics import analyze_all_patients
except ImportError:
    analyze_all_patients = None

# ==========================================
# HELPER DATA & FUNCTIONS FOR SEEDING
# ==========================================

FIRST_NAMES = ['Suresh', 'Ravi', 'Satish', 'Lakshmi', 'Sita', 'Ramesh', 'Kavya', 'Priya', 'Manish', 'Anjali', 
              'Venkatesh', 'Sowmya', 'Harsha', 'Divya', 'Naveen', 'Shruti', 'Bhavya', 'Krishna', 'Rajesh', 'Meena', 
              'Chiranjeevi', 'Swathi', 'Teja', 'Aparna', 'Gowtham']
LAST_NAMES = ['Reddy', 'Naidu', 'Prasad', 'Kumar', 'Rao', 'Chowdary', 'Raju', 'Murthy', 'Gowda', 'Sharma', 
             'Patel', 'Singh', 'Iyer', 'Nair', 'Desai']
SPEC_LIST = ['Cardiology', 'Neurology', 'General Medicine', 'Pediatrics', 'Emergency Medicine', 'Surgery', 'Oncology', 'Orthopedics']
SAMPLE_ADDRESSES = ["12 Baker Street, Springfield", "45 Greenway Ave, Rivertown", "78 Sunset Blvd, Lakeview", "3 Elm St, Oldtown"]
SAMPLE_DIAGNOSES = ["Community acquired pneumonia", "Uncontrolled hypertension", "Acute myocardial ischemia", 
                   "Sepsis", "Post-op monitoring", "Chronic obstructive pulmonary disease exacerbation", "Stroke - suspected"]

def ensure_sample_staff():
    """Create deterministic sample staff (25 doctors, 60 nurses)"""
    # Clear existing non-admin staff
    StaffMember.query.filter(StaffMember.role != 'admin').delete()
    db.session.commit()
    
    sample_staff = []
    # Create 25 doctors
    for i in range(1, 26):
        fn = FIRST_NAMES[(i-1) % len(FIRST_NAMES)]
        ln = LAST_NAMES[(i-1) % len(LAST_NAMES)]
        spec = SPEC_LIST[(i-1) % len(SPEC_LIST)]
        sample_staff.append({
            'staff_id': f'DOC{i:04d}',
            'first_name': fn, 'last_name': ln, 'email': f'doc{i:02d}@example.com',
            'role': 'doctor', 'specialization': spec, 'department': spec
        })
    # Create 60 nurses
    for i in range(1, 61):
        fn = FIRST_NAMES[(i-1) % len(FIRST_NAMES)]
        ln = LAST_NAMES[(i-1) % len(LAST_NAMES)]
        sample_staff.append({
            'staff_id': f'NRS{i:04d}',
            'first_name': fn, 'last_name': ln, 'email': f'nrs{i:02d}@example.com',
            'role': 'nurse', 'department': spec if 'spec' in locals() else 'Nursing' # Nurses don't have spec, but might have dept
        })

    for s in sample_staff:
        sm = StaffMember(
            staff_id=s['staff_id'], first_name=s['first_name'], last_name=s['last_name'],
            email=s['email'], role=s['role'], specialization=s.get('specialization'), 
            department=s.get('department') or s.get('specialization'), is_active=True
        )
        sm.set_password('password')
        db.session.add(sm)
    db.session.commit()
    print(f'Seeded {len(sample_staff)} staff members')

def pick_staff_by_specialization(specialization):
    doctors = StaffMember.query.filter_by(role='doctor', is_active=True, specialization=specialization).all()
    if not doctors:
        doctors = StaffMember.query.filter_by(role='doctor', is_active=True).all()
    if not doctors: return None
    # Pick least loaded
    best, best_count = None, None
    for d in doctors:
        cnt = Patient.query.filter_by(assigned_doctor_id=d.id).count()
        if best is None or cnt < best_count:
            best, best_count = d, cnt
    return best

def pick_nurse_for_patient():
    nurses = StaffMember.query.filter_by(role='nurse', is_active=True).all()
    if not nurses: return None
    best, best_count = None, None
    for n in nurses:
        cnt = Patient.query.filter_by(assigned_nurse_id=n.id).count()
        if best is None or cnt < best_count:
            best, best_count = n, cnt
    return best

def ai_recommend_specialization(patient_summary):
    if groq_client is None: return None
    try:
        prompt = f"Given the following patient summary, recommend a single medical specialization best suited to manage this patient from the list: {SPEC_LIST}. Patient: {patient_summary}\nAnswer with the specialization name only."
        text = generate_ai_response([{'role': 'user', 'content': prompt}])
        if not text:
            return None
        
        text_low = text.lower()
        for s in SPEC_LIST:
            if s.lower() in text_low: return s
        # Fallback keywords
        if 'cardio' in text_low: return 'Cardiology'
        if 'neuro' in text_low: return 'Neurology'
        if 'emerg' in text_low or 'icu' in text_low: return 'Emergency Medicine'
    except Exception:
        pass
    return None

def determine_specialization_heuristic(diagnosis):
    diag_low = (diagnosis or '').lower()
    if 'cardio' in diag_low or 'myocard' in diag_low or 'hypertension' in diag_low: return 'Cardiology'
    if 'neuro' in diag_low or 'stroke' in diag_low: return 'Neurology'
    if 'pneum' in diag_low or 'copd' in diag_low or 'sepsis' in diag_low: return 'Emergency Medicine'
    if 'post-op' in diag_low or 'surgery' in diag_low: return 'Surgery'
    return 'General Medicine'

# ==========================================
# COMMAND HANDLERS
# ==========================================

def cmd_reset_staff_password(staff_id, new_pass):
    with app.app_context():
        s = StaffMember.query.filter_by(staff_id=staff_id).first()
        if not s:
            print("Staff not found:", staff_id)
            sys.exit(1)
        s.set_password(new_pass)
        db.session.commit()
        print("Password updated for", staff_id)

def cmd_reset_demo_passwords():
    with app.app_context():
        print("Resetting all Doctor and Nurse passwords to 'password123'...")
        doctors = StaffMember.query.filter_by(role='doctor').all()
        for d in doctors: d.set_password('password123')
        nurses = StaffMember.query.filter_by(role='nurse').all()
        for n in nurses: n.set_password('password123')
        
        admin = StaffMember.query.filter_by(role='admin').first()
        if admin:
            admin.set_password('admin123')
            print(f"Admin {admin.staff_id} password set to 'admin123'")
            
        db.session.commit()
        print(f"Updated passwords for {len(doctors)} doctors and {len(nurses)} nurses.")

def cmd_create_tables():
    with app.app_context():
        db.create_all()
        print("Database tables updated.")

def cmd_init_db(args):
    with app.app_context():
        db_path = 'patient_care_dev.db'
        if args.delete_db and os.path.exists(db_path):
            try:
                os.remove(db_path)
                print(f"✓ Deleted existing database: {db_path}")
            except Exception as e:
                print(f"⚠ Could not delete database: {e}")
                
        # Drop and create
        try:
            db.drop_all()
            print("[OK] Dropped existing tables")
        except Exception: pass
        
        db.create_all()
        print("[OK] Created tables")
        
        initialize_synthetic_data(
            num_doctors=args.doctors,
            num_nurses=args.nurses,
            num_patients=args.patients,
            num_discharged=args.discharged
        )
        print(f"[OK] Generated synthetic data")
        
        # Verify
        from sqlalchemy import inspect
        inspector = inspect(db.engine)
        if 'chat_messages' in inspector.get_table_names():
            print("✓ ChatMessage table verified")

def cmd_seed_wards():
    with app.app_context():
        try:
            standard_wards = ['General Ward', 'Emergency', 'ICU', 'Pediatrics', 'Cardiology', 'Neurology', 'Orthopedics', 'Oncology', 'Surgery']
            existing_depts = db.session.query(Patient.department).distinct().filter(Patient.department.isnot(None)).all()
            dept_names = [d[0] for d in existing_depts]
            all_wards = set(dept_names + standard_wards)
            
            added_count = 0
            for i, name in enumerate(all_wards):
                if not name or Ward.query.filter_by(name=name).first(): continue
                
                head_nurse = StaffMember.query.filter_by(role='nurse', department=name).first()
                ward = Ward(name=name, capacity=30, floor=f"{i % 5 + 1}th Floor", head_nurse_id=head_nurse.id if head_nurse else None)
                db.session.add(ward)
                added_count += 1
            
            db.session.commit()
            print(f"Seeded {added_count} new wards.")
        except Exception as e:
            print(f"Error seeding wards: {e}")

# ==========================================
# COMMAND HANDLERS
# ==========================================

def cmd_reset_staff_password(staff_id, new_pass):
    with app.app_context():
        s = StaffMember.query.filter_by(staff_id=staff_id).first()
        if not s:
            print("Staff not found:", staff_id)
            sys.exit(1)
        s.set_password(new_pass)
        db.session.commit()
        print("Password updated for", staff_id)

def cmd_reset_demo_passwords():
    with app.app_context():
        print("Resetting all Doctor and Nurse passwords to 'password123'...")
        doctors = StaffMember.query.filter_by(role='doctor').all()
        for d in doctors: d.set_password('password123')
        nurses = StaffMember.query.filter_by(role='nurse').all()
        for n in nurses: n.set_password('password123')
        
        admin = StaffMember.query.filter_by(role='admin').first()
        if admin:
            admin.set_password('admin123')
            print(f"Admin {admin.staff_id} password set to 'admin123'")
            
        db.session.commit()
        print(f"Updated passwords for {len(doctors)} doctors and {len(nurses)} nurses.")

def cmd_create_tables():
    with app.app_context():
        db.create_all()
        print("Database tables updated.")

def cmd_init_db(args):
    with app.app_context():
        db_path = 'patient_care_dev.db'
        if args.delete_db and os.path.exists(db_path):
            try:
                os.remove(db_path)
                print(f"✓ Deleted existing database: {db_path}")
            except Exception as e:
                print(f"⚠ Could not delete database: {e}")
                
        # Drop and create
        try:
            db.drop_all()
            print("[OK] Dropped existing tables")
        except Exception: pass
        
        db.create_all()
        print("[OK] Created tables")
        
        initialize_synthetic_data(
            num_doctors=args.doctors,
            num_nurses=args.nurses,
            num_patients=args.patients,
            num_discharged=args.discharged
        )
        print(f"[OK] Generated synthetic data")
        
        # Verify
        from sqlalchemy import inspect
        inspector = inspect(db.engine)
        if 'chat_messages' in inspector.get_table_names():
            print("✓ ChatMessage table verified")

def cmd_seed_wards():
    with app.app_context():
        try:
            standard_wards = ['General Ward', 'Emergency', 'ICU', 'Pediatrics', 'Cardiology', 'Neurology', 'Orthopedics', 'Oncology', 'Surgery']
            existing_depts = db.session.query(Patient.department).distinct().filter(Patient.department.isnot(None)).all()
            dept_names = [d[0] for d in existing_depts]
            all_wards = set(dept_names + standard_wards)
            
            added_count = 0
            for i, name in enumerate(all_wards):
                if not name or Ward.query.filter_by(name=name).first(): continue
                
                head_nurse = StaffMember.query.filter_by(role='nurse', department=name).first()
                ward = Ward(name=name, capacity=30, floor=f"{i % 5 + 1}th Floor", head_nurse_id=head_nurse.id if head_nurse else None)
                db.session.add(ward)
                added_count += 1
            
            db.session.commit()
            print(f"Seeded {added_count} new wards.")
        except Exception as e:
            print(f"Error seeding wards: {e}")

def cmd_seed_scenarios():
    """Runs the advanced scenario seeding from reset_and_seed.py"""
    with app.app_context():
        # ... (same implementation as before)
        print('Clearing existing patient-related data...')
        db.session.query(Alert).delete()
        db.session.query(RiskAssessment).delete()
        db.session.query(VitalSign).delete()
        db.session.query(ChatMessage).delete()
        db.session.query(Medication).delete()
        db.session.query(Patient).delete()
        db.session.commit()

        ensure_sample_staff()

        print('Seeding sample patients and vitals...')
        now = datetime.now()
        
        sample_patients = [
            {'pid': 'PAT000001', 'fn': 'Stable', 'ln': 'Patient', 'dob': datetime(1980,1,1).date(), 'gender': 'female', 'status': 'admitted'},
            {'pid': 'PAT000002', 'fn': 'Moderate', 'ln': 'Risk', 'dob': datetime(1975,6,15).date(), 'gender': 'male', 'status': 'admitted'},
            {'pid': 'PAT000003', 'fn': 'Critical', 'ln': 'Patient', 'dob': datetime(1950,3,20).date(), 'gender': 'male', 'status': 'icu'}
        ]

        for p in sample_patients:
            patient = Patient(patient_id=p['pid'], first_name=p['fn'], last_name=p['ln'], date_of_birth=p['dob'], gender=p['gender'], status=p['status'])
            db.session.add(patient)
            db.session.flush()
            
            patient.address = random.choice(SAMPLE_ADDRESSES)
            patient.diagnosis = random.choice(SAMPLE_DIAGNOSES)
            
            try: age = patient.age
            except: age = 'unknown'
            
            specialization = ai_recommend_specialization(f"{patient.full_name}, {age} years, {patient.status}. {patient.diagnosis}")
            if not specialization: specialization = determine_specialization_heuristic(patient.diagnosis)
            
            doctor = pick_staff_by_specialization(specialization)
            nurse = pick_nurse_for_patient()
            if doctor: patient.assigned_doctor_id = doctor.id
            if nurse: patient.assigned_nurse_id = nurse.id
            
            patient.room_number = f"A-{random.randint(1,10)}"
            patient.bed_number = str(random.randint(1,20))
            db.session.add(patient)
            db.session.flush()

            # Add vitals
            vitals = []
            for i in range(8):
                rec_time = now - timedelta(minutes=5*i)
                if p['pid'] == 'PAT000001':
                    v = VitalSign(patient_id=patient.id, heart_rate=random.randint(65,80), blood_pressure_systolic=random.randint(110,125), blood_pressure_diastolic=random.randint(70,80), oxygen_saturation=random.uniform(96,99), temperature=random.uniform(97.5,99.0), respiratory_rate=random.randint(14,18), recorded_at=rec_time)
                elif p['pid'] == 'PAT000002':
                    v = VitalSign(patient_id=patient.id, heart_rate=80+i*2, blood_pressure_systolic=115+(i%3), blood_pressure_diastolic=75+(i%2), oxygen_saturation=95-i*0.5, temperature=98.0+(i%2)*0.2, respiratory_rate=16+(i%2), recorded_at=rec_time)
                else: # Critical
                    v = VitalSign(patient_id=patient.id, heart_rate=120+i, blood_pressure_systolic=170-i, blood_pressure_diastolic=100-i, oxygen_saturation=82+i*0.5, temperature=101+(i%3)*0.6, respiratory_rate=28+(i%2)*2, recorded_at=rec_time)
                vitals.append(v)
            db.session.add_all(vitals)

        # Seed random additional patients to reach 55
        target = 55
        existing = 3
        next_idx = 4
        
        while existing < target:
            pid = f"PAT{next_idx:06d}"
            fn = random.choice(['John','Jane','Sam','Sara','Liam','Noah','Olivia','Emma','Lucas','Mia'])
            ln = random.choice(['Smith','Johnson','Lee','Brown','Garcia','Martinez','Davis'])
            patient = Patient(
                patient_id=pid, first_name=fn, last_name=ln,
                date_of_birth=datetime.now().date().replace(year=random.randint(1940,2005)),
                gender=random.choice(['male','female']),
                status=random.choice(['admitted','icu','emergency'])
            )
            db.session.add(patient)
            db.session.flush()
            patient.address = random.choice(SAMPLE_ADDRESSES)
            patient.diagnosis = random.choice(SAMPLE_DIAGNOSES)
            
            specialization = determine_specialization_heuristic(patient.diagnosis)
            doc = pick_staff_by_specialization(specialization)
            nurse = pick_nurse_for_patient()
            if doc: patient.assigned_doctor_id = doc.id
            if nurse: patient.assigned_nurse_id = nurse.id
            
            patient.room_number = f"{random.choice(['A','B'])}-{random.randint(1,20)}"
            patient.bed_number = str(random.randint(1,20))
            db.session.add(patient)
            
            # Vitals
            base_hr = random.randint(60,120)
            for i in range(8):
                db.session.add(VitalSign(
                    patient_id=patient.id, heart_rate=base_hr+random.randint(-5,5),
                    blood_pressure_systolic=random.randint(100,160), blood_pressure_diastolic=random.randint(60,100),
                    oxygen_saturation=random.uniform(90,99), temperature=98+random.uniform(-1,2),
                    respiratory_rate=random.randint(12,25), recorded_at=now-timedelta(minutes=5*i)
                ))
            
            existing += 1
            next_idx += 1
            
        db.session.commit()
        
        if analyze_all_patients:
            print('Running AI risk analysis...')
            analyze_all_patients()
        
        print('Scenario seeding complete.')

def cmd_check_data():
    with app.app_context():
        doctors = StaffMember.query.filter_by(role='doctor').count()
        nurses = StaffMember.query.filter_by(role='nurse').count()
        patients = Patient.query.count()
        print(f"Data Status:\n  Doctors: {doctors}\n  Nurses: {nurses}\n  Patients: {patients}")
        admin = StaffMember.query.filter_by(role='admin').first()
        if admin:
            print(f"  Admin: {admin.full_name} ({admin.email})")

def cmd_run_analysis():
    with app.app_context():
        from predictive_analytics import risk_predictor
        import json
        patients = Patient.query.filter(Patient.status.in_(['admitted', 'icu', 'emergency'])).all()
        results = []
        for p in patients:
            analysis = risk_predictor.analyze_patient_risk(p.id)
            results.append({'id':p.id, 'name':p.full_name, 'room':p.room_number, **analysis})
        print(json.dumps(results, indent=2, default=str))


def start_presentation_alert_loop():
    """Keep the presentation dashboards supplied with live demo events."""
    if os.environ.get('DEMO_ALERTS', '1').lower() not in ('1', 'true', 'yes'):
        print('Presentation alert loop disabled (set DEMO_ALERTS=1 to enable).')
        return

    from vital_simulator import generate_presentation_alerts

    def run_loop():
        while True:
            try:
                result = generate_presentation_alerts(force=True)
                logging.info('Presentation alert cycle: %s', result)
            except Exception:
                logging.exception('Presentation alert cycle failed')
            time.sleep(int(os.environ.get('DEMO_ALERT_INTERVAL', '15')))

    thread = threading.Thread(target=run_loop, name='presentation-alert-loop', daemon=True)
    thread.start()
    print('Presentation alert loop started.')

# ==========================================
# MAIN ENTRY POINT
# ==========================================

if __name__ == "__main__":
    # Check dependencies
    if not os.path.exists("models.py") or not os.path.exists("routes.py"):
        print("Error: models.py or routes.py not found in current directory.")
        sys.exit(1)

    if len(sys.argv) > 1:
        parser = argparse.ArgumentParser(description="CareSync Management CLI")
        subparsers = parser.add_subparsers(dest='command', help='Command to run')

        # reset-password
        p_pass = subparsers.add_parser('reset-password', help='Reset staff password')
        p_pass.add_argument('staff_id', help='Staff ID (e.g., DOC0001)')
        p_pass.add_argument('new_password', help='New Password')

        # reset-demo-passwords
        subparsers.add_parser('reset-demo-passwords', help='Reset all demo passwords to defaults')

        # create-tables
        subparsers.add_parser('create-tables', help='Create DB tables')

        # seed-wards
        subparsers.add_parser('seed-wards', help='Seed ward data')

        # init-db
        p_init = subparsers.add_parser('init-db', help='Initialize DB with synthetic data')
        p_init.add_argument('--doctors', type=int, default=30)
        p_init.add_argument('--nurses', type=int, default=60)
        p_init.add_argument('--patients', type=int, default=70)
        p_init.add_argument('--discharged', type=int, default=20)
        p_init.add_argument('--delete-db', action='store_true')

        # seed-scenarios
        subparsers.add_parser('seed-scenarios', help='Reset DB and seed specific test scenarios')

        # check-data
        subparsers.add_parser('check-data', help='Check data counts')

        # run-analysis
        subparsers.add_parser('run-analysis', help='Run predictive risk analysis')

        args = parser.parse_args()

        if args.command == 'reset-password':
            cmd_reset_staff_password(args.staff_id, args.new_password)
        elif args.command == 'reset-demo-passwords':
            cmd_reset_demo_passwords()
        elif args.command == 'create-tables':
            cmd_create_tables()
        elif args.command == 'seed-wards':
            cmd_seed_wards()
        elif args.command == 'init-db':
            cmd_init_db(args)
        elif args.command == 'seed-scenarios':
            cmd_seed_scenarios()
        elif args.command == 'check-data':
            cmd_check_data()
        elif args.command == 'run-analysis':
            cmd_run_analysis()
        else:
            parser.print_help()
            
    else:
        # Default behavior: Run Server
        debug_mode = os.environ.get("FLASK_DEBUG", "0") in ("1", "true", "True")
        print("Starting CareSync Server...")
        start_presentation_alert_loop()
        socketio.run(app, host="0.0.0.0", port=5000, debug=debug_mode, allow_unsafe_werkzeug=True)
