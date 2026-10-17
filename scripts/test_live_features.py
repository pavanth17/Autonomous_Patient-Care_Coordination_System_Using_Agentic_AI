import requests
import json
import sys

BASE_URL = "http://127.0.0.1:5000"

def test_live_server():
    print("=" * 80)
    print("TESTING LIVE SERVER AT", BASE_URL)
    print("=" * 80)

    # 1. Test Public Pages
    public_endpoints = [
        ("/", 200, "CareSync"),
        ("/health", 200, "status"),
        ("/login", 200, "Sign in"),
        ("/patient-login", 200, "Patient Portal"),
        ("/credentials", 200, "Credentials"),
    ]

    for path, expected_code, expected_content in public_endpoints:
        r = requests.get(BASE_URL + path)
        ok = r.status_code == expected_code and (expected_content.lower() in r.text.lower())
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] GET {path} -> {r.status_code} (Contains '{expected_content}': {expected_content.lower() in r.text.lower()})")

    # 2. Test Admin Flow
    s_admin = requests.Session()
    login_res = s_admin.post(BASE_URL + "/login", data={
        "staff_id": "ADM0001",
        "password": "admin" if False else "admin123"
    }, allow_redirects=True)
    admin_logged_in = "logout" in login_res.text.lower() or "admin" in login_res.url
    print(f"[{'PASS' if admin_logged_in else 'FAIL'}] Admin Login ADM0001 -> URL: {login_res.url}, Logged in: {admin_logged_in}")

    admin_pages = [
        ("/admin", "Admin Dashboard"),
        ("/admin/users", "Staff"),
        ("/admin/patients", "Patients"),
        ("/admin/appointments", "Appointment"),
        ("/admin/wards", "Ward"),
        ("/admin/assign-staff", "Assign"),
        ("/admin/rerouting-logs", "Rerouting"),
    ]
    for path, expected_text in admin_pages:
        r = s_admin.get(BASE_URL + path)
        ok = r.status_code == 200 and expected_text.lower() in r.text.lower()
        print(f"[{'PASS' if ok else 'FAIL'}] Admin GET {path} -> {r.status_code} (Contains '{expected_text}': {expected_text.lower() in r.text.lower()})")

    # 3. Test Doctor Flow
    s_doc = requests.Session()
    doc_login_res = s_doc.post(BASE_URL + "/login", data={
        "staff_id": "DOC0001",
        "password": "password123"
    }, allow_redirects=True)
    doc_logged_in = "doctor" in doc_login_res.url or "logout" in doc_login_res.text.lower()
    print(f"[{'PASS' if doc_logged_in else 'FAIL'}] Doctor Login DOC0001 -> URL: {doc_login_res.url}, Logged in: {doc_logged_in}")

    doc_pages = [
        ("/doctor", "Doctor"),
        ("/shifts", "Shift"),
        ("/api/alerts/active", ""),
        ("/api/risk-analysis/all", ""),
    ]
    for path, expected_text in doc_pages:
        r = s_doc.get(BASE_URL + path)
        ok = r.status_code == 200 and (not expected_text or expected_text.lower() in r.text.lower())
        print(f"[{'PASS' if ok else 'FAIL'}] Doctor GET {path} -> {r.status_code}")

    # 4. Test Nurse Flow
    s_nurse = requests.Session()
    nurse_login_res = s_nurse.post(BASE_URL + "/login", data={
        "staff_id": "NRS0001",
        "password": "password123"
    }, allow_redirects=True)
    nurse_logged_in = "nurse" in nurse_login_res.url or "logout" in nurse_login_res.text.lower()
    print(f"[{'PASS' if nurse_logged_in else 'FAIL'}] Nurse Login NRS0001 -> URL: {nurse_login_res.url}, Logged in: {nurse_logged_in}")

    nurse_pages = [
        ("/nurse", "Nurse"),
        ("/nurse/patient-lookup", "Patient"),
        ("/api/nurse/notifications", ""),
        ("/shifts", "Shift"),
        ("/handoff/create", "Handoff"),
    ]
    for path, expected_text in nurse_pages:
        r = s_nurse.get(BASE_URL + path)
        ok = r.status_code == 200 and (not expected_text or expected_text.lower() in r.text.lower())
        print(f"[{'PASS' if ok else 'FAIL'}] Nurse GET {path} -> {r.status_code}")

    # 5. Test Patient Flow
    s_patient = requests.Session()
    pat_login_res = s_patient.post(BASE_URL + "/patient-login", data={
        "patient_id": "PAT000002",
        "phone": "9700443157"
    }, allow_redirects=True)
    pat_logged_in = "dashboard" in pat_login_res.url or "patient" in pat_login_res.url
    print(f"[{'PASS' if pat_logged_in else 'FAIL'}] Patient Login PAT000002 -> URL: {pat_login_res.url}, Logged in: {pat_logged_in}")

    pat_pages = [
        ("/patient-portal/dashboard", "Dashboard"),
        ("/patient-portal/history", "History"),
        ("/patient-portal/medications", "Medication"),
        ("/patient-portal/appointments", "Appointment"),
        ("/patient-portal/chat", "Chat"),
        ("/patient-portal/medical-records", "Records"),
    ]
    for path, expected_text in pat_pages:
        r = s_patient.get(BASE_URL + path)
        ok = r.status_code == 200 and expected_text.lower() in r.text.lower()
        print(f"[{'PASS' if ok else 'FAIL'}] Patient GET {path} -> {r.status_code} (Contains '{expected_text}': {expected_text.lower() in r.text.lower()})")

    # 6. Test Key APIs
    print("-" * 50)
    print("Testing APIs:")

    # Arduino Vitals API
    r_arduino = requests.post(BASE_URL + "/api/arduino/vitals", json={
        "patient_id": 2,
        "bpm": 76,
        "spo2": 98,
        "temp": 98.6
    })
    print(f"[{'PASS' if r_arduino.status_code == 200 else 'FAIL'}] POST /api/arduino/vitals -> {r_arduino.status_code}: {r_arduino.text[:100]}")

    # Translations API
    r_trans = requests.get(BASE_URL + "/api/appointment/translations/en")
    print(f"[{'PASS' if r_trans.status_code == 200 else 'FAIL'}] GET /api/appointment/translations/en -> {r_trans.status_code}")

    # Medicine Details API
    r_med = requests.get(BASE_URL + "/api/medicine-details/Paracetamol")
    print(f"[{'PASS' if r_med.status_code == 200 else 'FAIL'}] GET /api/medicine-details/Paracetamol -> {r_med.status_code}")

    # Discharged Summary API
    r_disc = s_patient.get(BASE_URL + "/api/discharged/summary")
    print(f"[{'PASS' if r_disc.status_code in [200, 302] else 'FAIL'}] GET /api/discharged/summary -> {r_disc.status_code}")

    # Patient AI Chat API
    r_chat = s_patient.post(BASE_URL + "/api/patient/2/chat", json={
        "message": "Hello, how are my latest vitals?",
        "language": "en"
    })
    print(f"[{'PASS' if r_chat.status_code == 200 else 'FAIL'}] POST /api/patient/2/chat -> {r_chat.status_code}: {r_chat.text[:120]}")

    print("=" * 80)
    print("LIVE SERVER AUDIT COMPLETE")
    print("=" * 80)

if __name__ == '__main__':
    test_live_server()
