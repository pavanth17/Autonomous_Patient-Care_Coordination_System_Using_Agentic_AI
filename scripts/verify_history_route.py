from app import app, db
from models import StaffMember, Patient

def verify_history():
    with app.app_context():
        # Ensure we have a staff member and a patient
        staff = StaffMember.query.first()
        patient = Patient.query.first()
        
        if not staff or not patient:
            print("Error: Database missing staff or patient data for testing.")
            return

        print(f"Testing with Staff ID: {staff.id}, Patient ID: {patient.id}")

        client = app.test_client()
        with client.session_transaction() as sess:
            sess['staff_id'] = staff.id
            sess['staff_role'] = staff.role

        try:
            resp = client.get(f'/patient/{patient.id}/history')
            print(f"Response Code: {resp.status_code}")
            
            if resp.status_code == 200:
                content = resp.data.decode('utf-8')
                if "Patient History" in content and "Doctor Notes" in content:
                    print("SUCCESS: Route accessible and verified content present.")
                else:
                    print("WARNING: Route accessible but expected content missing.")
            else:
                print("FAILURE: Route returned unexpected status code.")
                
        except Exception as e:
            print(f"FAILURE: Exception occurred: {e}")

if __name__ == "__main__":
    verify_history()
