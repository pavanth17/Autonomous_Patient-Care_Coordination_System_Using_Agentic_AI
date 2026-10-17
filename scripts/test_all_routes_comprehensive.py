import sys
import os
import unittest
from datetime import datetime, timedelta

# Ensure parent directory in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import app, db
from models import StaffMember, Patient, VitalSign, Alert, Medication, Round, Shift, ShiftHandoff, AppointmentRequest, Notification, MedicineDetail

def run_all_route_tests():
    print("=" * 80)
    print("COMPREHENSIVE ROUTE AND FEATURE AUDIT")
    print("=" * 80)

    client = app.test_client()
    results = []

    with app.app_context():
        # Retrieve test entities
        admin = StaffMember.query.filter_by(role='admin').first()
        doctor = StaffMember.query.filter_by(role='doctor').first()
        nurse = StaffMember.query.filter_by(role='nurse').first()
        patient = Patient.query.first()
        discharged_patient = Patient.query.filter_by(status='discharged').first()
        if not discharged_patient and patient:
            # Create or mark one for test
            discharged_patient = patient

        alert = Alert.query.first()
        med = Medication.query.first()
        round_obj = Round.query.first()
        shift = Shift.query.first()
        handoff = ShiftHandoff.query.first()
        appt = AppointmentRequest.query.first()
        notif = Notification.query.first()
        med_detail = MedicineDetail.query.first()

        print(f"Entities loaded: Admin={bool(admin)}, Doctor={bool(doctor)}, Nurse={bool(nurse)}, Patient={bool(patient)}")

        # Helper to test a route
        def test_endpoint(name, method, url, data=None, json_data=None, login_as=None, expected_status=[200, 302]):
            try:
                # Login context
                with client.session_transaction() as sess:
                    sess.clear()
                    if login_as == 'admin' and admin:
                        sess['_user_id'] = str(admin.id)
                        sess['user_id'] = admin.id
                        sess['role'] = 'admin'
                    elif login_as == 'doctor' and doctor:
                        sess['_user_id'] = str(doctor.id)
                        sess['user_id'] = doctor.id
                        sess['role'] = 'doctor'
                    elif login_as == 'nurse' and nurse:
                        sess['_user_id'] = str(nurse.id)
                        sess['user_id'] = nurse.id
                        sess['role'] = 'nurse'
                    elif login_as == 'patient' and patient:
                        sess['patient_id'] = patient.id
                        sess['patient_name'] = patient.full_name
                        sess['is_discharged'] = False
                    elif login_as == 'discharged' and discharged_patient:
                        sess['patient_id'] = discharged_patient.id
                        sess['patient_name'] = discharged_patient.full_name
                        sess['is_discharged'] = True

                if method == 'GET':
                    res = client.get(url, follow_redirects=False)
                elif method == 'POST':
                    if json_data is not None:
                        res = client.post(url, json=json_data, follow_redirects=False)
                    else:
                        res = client.post(url, data=data or {}, follow_redirects=False)

                is_ok = res.status_code in expected_status
                msg = f"[{'PASS' if is_ok else 'FAIL'}] {method} {url} -> {res.status_code} (User: {login_as})"
                if not is_ok:
                    msg += f"\n    Response snippet: {res.data.decode('utf-8', errors='ignore')[:300]}"
                results.append((is_ok, msg))
                print(msg)
                return res
            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                msg = f"[ERROR] {method} {url} (User: {login_as}): {e}\n{tb}"
                results.append((False, msg))
                print(msg)
                return None

        # 1. PUBLIC ROUTES
        test_endpoint("Index", "GET", "/")
        test_endpoint("Health Check", "GET", "/health")
        test_endpoint("Login Page", "GET", "/login")
        test_endpoint("Init Data", "GET", "/init-data")
        test_endpoint("Credentials Page", "GET", "/credentials")
        test_endpoint("Patient Login Page", "GET", "/patient-login")

        # 2. ADMIN ROUTES
        test_endpoint("Admin Dashboard (as Admin)", "GET", "/admin", login_as='admin')
        test_endpoint("Admin Rerouting Logs", "GET", "/admin/rerouting-logs", login_as='admin')
        test_endpoint("Admin Users", "GET", "/admin/users", login_as='admin')
        test_endpoint("Admin Patients", "GET", "/admin/patients", login_as='admin')
        test_endpoint("Admin Appointments", "GET", "/admin/appointments", login_as='admin')
        test_endpoint("Admin Wards", "GET", "/admin/wards", login_as='admin')
        test_endpoint("Admin Assign Staff", "GET", "/admin/assign-staff", login_as='admin')
        if doctor:
            test_endpoint("Admin Staff Patients", "GET", f"/admin/staff/{doctor.id}/patients", login_as='admin')
            test_endpoint("Toggle Duty", "POST", f"/admin/staff/{doctor.id}/toggle-duty", login_as='admin')
            test_endpoint("Toggle Active", "POST", f"/admin/staff/{doctor.id}/toggle-active", login_as='admin')

        # 3. DOCTOR ROUTES
        test_endpoint("Doctor Dashboard", "GET", "/doctor", login_as='doctor')
        if patient:
            test_endpoint("Doctor Add Note Page", "GET", f"/doctor/note/{patient.id}/add", login_as='doctor')
            test_endpoint("Doctor Add Note Submit", "POST", f"/doctor/note/{patient.id}/add",
                          data={'note_type': 'general', 'content': 'Test automated check note'}, login_as='doctor')
            test_endpoint("Schedule Round", "POST", "/doctor/schedule-round",
                          data={'patient_id': patient.id, 'scheduled_time': (datetime.now() + timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M')},
                          login_as='doctor')
        if round_obj:
            test_endpoint("Complete Round", "POST", f"/doctor/round/{round_obj.id}/complete",
                          data={'notes': 'Completed in test'}, login_as='doctor')

        # 4. NURSE ROUTES
        test_endpoint("Nurse Dashboard", "GET", "/nurse", login_as='nurse')
        test_endpoint("Nurse Patient Lookup", "GET", "/nurse/patient-lookup", login_as='nurse')
        test_endpoint("Nurse Notifications API", "GET", "/api/nurse/notifications", login_as='nurse')
        if patient:
            test_endpoint("Nurse Update Patient", "POST", f"/nurse/update-patient/{patient.id}",
                          data={'notes': 'Updated nurse notes test'}, login_as='nurse')

        # 5. PATIENT CLINICAL DETAIL & ACTIONS (STAFF)
        if patient:
            test_endpoint("Patient Detail", "GET", f"/patient/{patient.id}", login_as='doctor')
            test_endpoint("Patient History", "GET", f"/patient/{patient.id}/history", login_as='doctor')
            test_endpoint("Patient Medications", "GET", f"/patient/{patient.id}/medications", login_as='nurse')
            test_endpoint("Medication Schedule", "GET", f"/medication/{patient.id}/schedule", login_as='doctor')
            test_endpoint("Patient Risk Analysis Page", "GET", f"/patient/{patient.id}/risk-analysis", login_as='doctor')
            test_endpoint("API Patient Risk Analysis", "GET", f"/api/risk-analysis/{patient.id}", login_as='doctor')
            test_endpoint("API Patient Vitals", "GET", f"/api/patient/{patient.id}/vitals", login_as='doctor')
            test_endpoint("API Patient Medical Records", "GET", f"/api/patient/{patient.id}/medical-records", login_as='doctor')
            test_endpoint("Add Treatment Page", "GET", f"/patient/{patient.id}/add-treatment", login_as='doctor')

        # 6. API ALERTS & VITALS
        test_endpoint("API Active Alerts", "GET", "/api/alerts/active", login_as='doctor')
        test_endpoint("API All Risk Analysis", "GET", "/api/risk-analysis/all", login_as='doctor')
        if alert:
            test_endpoint("Acknowledge Alert", "POST", f"/alert/{alert.id}/acknowledge", login_as='doctor')
        test_endpoint("API Trigger Emergency Alert", "POST", "/api/trigger-emergency-alert",
                      json_data={'patient_id': patient.id if patient else 1, 'vital_type': 'heart_rate', 'value': 140, 'severity': 'critical'},
                      login_as='doctor')
        test_endpoint("API Arduino Vitals", "POST", "/api/arduino/vitals",
                      json_data={'patient_id': patient.id if patient else 1, 'heart_rate': 78, 'oxygen_saturation': 98, 'temperature': 98.6},
                      login_as='doctor')

        # 7. MEDICATIONS
        if med:
            test_endpoint("Administer Medication", "POST", f"/medication/{med.id}/administer",
                          data={'notes': 'Dose administered test'}, login_as='nurse')
            test_endpoint("Deactivate Medication API", "POST", f"/api/medication/{med.id}/deactivate",
                          json_data={'reason': 'Completed course'}, login_as='doctor')
        test_endpoint("Medicine Details API", "GET", "/api/medicine-details/Paracetamol")

        # 8. SHIFTS & HANDOFFS
        test_endpoint("Shift Management", "GET", "/shifts", login_as='nurse')
        test_endpoint("Create Shift", "POST", "/shifts/create",
                      data={'staff_id': nurse.id if nurse else 1, 'start_time': datetime.now().strftime('%Y-%m-%dT%H:%M'),
                            'end_time': (datetime.now() + timedelta(hours=8)).strftime('%Y-%m-%dT%H:%M'), 'ward_id': 1},
                      login_as='admin')
        if shift:
            test_endpoint("Shift Check In", "POST", f"/shifts/{shift.id}/check-in", login_as='nurse')
            test_endpoint("Shift Check Out", "POST", f"/shifts/{shift.id}/check-out", login_as='nurse')
        test_endpoint("Create Handoff Page", "GET", "/handoff/create", login_as='nurse')
        if handoff:
            test_endpoint("Acknowledge Handoff", "POST", f"/handoff/{handoff.id}/acknowledge", login_as='nurse')

        # 9. PATIENT PORTAL
        test_endpoint("Patient Portal Dashboard", "GET", "/patient-portal/dashboard", login_as='patient')
        test_endpoint("Patient Portal History", "GET", "/patient-portal/history", login_as='patient')
        test_endpoint("Patient Portal Medications", "GET", "/patient-portal/medications", login_as='patient')
        test_endpoint("Patient Portal Appointments", "GET", "/patient-portal/appointments", login_as='patient')
        test_endpoint("Patient Portal Chat", "GET", "/patient-portal/chat", login_as='patient')
        test_endpoint("Patient Portal Medical Records Page", "GET", "/patient-portal/medical-records", login_as='patient')
        test_endpoint("Discharged Dashboard", "GET", "/discharged-dashboard", login_as='discharged')
        test_endpoint("Discharged Summary API", "GET", "/api/discharged/summary", login_as='discharged')
        test_endpoint("Appointment Translations API", "GET", "/api/appointment/translations/en")

        # 10. NOTIFICATIONS
        if notif:
            test_endpoint("Acknowledge Notification", "POST", f"/api/notification/{notif.id}/acknowledge", login_as='doctor')
            test_endpoint("Mark Notification Read", "POST", f"/api/notification/{notif.id}/read", login_as='doctor')

        # 11. APPOINTMENTS
        if appt:
            test_endpoint("AI Routing Details API", "GET", f"/api/admin/appointment/{appt.id}/ai-routing-details", login_as='admin')
            test_endpoint("Confirm Appointment", "POST", f"/admin/appointment/{appt.id}/confirm",
                          json_data={'status': 'confirmed'}, login_as='admin')

        # 12. LOGOUTS
        test_endpoint("Staff Logout", "GET", "/logout", login_as='doctor')
        test_endpoint("Patient Logout", "GET", "/patient-logout", login_as='patient')
        test_endpoint("Discharged Logout", "GET", "/discharged-logout", login_as='discharged')

    # Summary
    passes = sum(1 for ok, _ in results if ok)
    fails = sum(1 for ok, _ in results if not ok)
    print("=" * 80)
    print(f"RESULTS: Total={len(results)}, Passed={passes}, Failed={fails}")
    print("=" * 80)

if __name__ == '__main__':
    run_all_route_tests()
