from app import app, db
from models import Patient
from datetime import datetime

def test_booking():
    with app.app_context():
        # Get a patient
        patient = Patient.query.first()
        if not patient:
            print("No patients found. Creating a test patient.")
            patient = Patient(
                first_name="Test", last_name="Patient",
                date_of_birth=datetime.strptime("1980-01-01", "%Y-%m-%d").date(),
                patient_id="PAT_TEST",
                email="test@test.com", phone="123",
                diagnosis="Test", status="outpatient"
            )
            db.session.add(patient)
            db.session.commit()
            
        print(f"Testing with patient ID: {patient.id}")
        
        # Test existing route
        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess['patient_id'] = patient.id
                
            data = {
                "preferred_date": "2024-12-25",
                "preferred_time": "10:00",
                "appointment_type": "routine",
                "department": "General",
                "urgency": "normal",
                "notes": "Test appointment",
                "language": "en"
            }
            
            try:
                resp = client.post(f'/api/patient/{patient.id}/book-appointment', json=data)
                
                print(f"Response status: {resp.status_code}")
                # Try to print JSON, fallback to text if fail
                try:
                    js = resp.get_json()
                    print(f"Response data: {js}")
                except:
                    print(f"Response text: {resp.text}")
                
            except Exception as e:
                print(f"FAILURE: Exception occurred: {e}")

if __name__ == "__main__":
    test_booking()
