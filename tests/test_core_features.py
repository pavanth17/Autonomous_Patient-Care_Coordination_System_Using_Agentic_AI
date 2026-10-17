"""Offline integration checks for the CareSync demo application.

These tests use an in-memory database and disable network-backed AI/SMS paths.
They verify the application-owned flows, not third-party credentials or hardware.
"""

import os

import pytest


# Configure the application before importing it.  The settings apply only to
# this pytest process, keeping developer data and external services untouched.
os.environ['DATABASE_URL'] = 'sqlite://'
os.environ['GROQ_API_KEY'] = ''
os.environ['DEVELOPMENT_MODE'] = 'true'

from app import app, db  # noqa: E402
from models import (  # noqa: E402
    AppointmentRequest,
    DoctorNote,
    Medication,
    Patient,
    Round,
    StaffMember,
    VitalSign,
)
from services import risk_predictor  # noqa: E402
from synthetic_data import initialize_synthetic_data  # noqa: E402


@pytest.fixture()
def client(monkeypatch):
    """A populated application client with no network side effects."""
    app.config.update(TESTING=True)
    monkeypatch.setattr('services.create_predictive_alert', lambda *args: None)

    with app.app_context():
        db.drop_all()
        db.create_all()
        initialize_synthetic_data(
            num_doctors=2,
            num_nurses=3,
            num_patients=5,
            num_discharged=1,
        )
        yield app.test_client()
        db.session.remove()
        db.drop_all()


def login_staff(client, staff_id, password):
    response = client.post(
        '/login',
        data={'staff_id': staff_id, 'password': password},
        follow_redirects=False,
    )
    assert response.status_code == 302


def first_active_patient():
    return Patient.query.filter(Patient.status != 'discharged').first()


def test_public_admin_and_risk_features(client):
    with app.app_context():
        patient = first_active_patient()
        doctor = StaffMember.query.filter_by(role='doctor').first()

        assert client.get('/health').get_json()['status'] == 'ok'
        assert client.get('/').status_code == 200
        assert client.get('/credentials').status_code == 200
        assert client.get('/api/medicine-details/Paracetamol').status_code == 200
        assert client.get('/api/appointment/translations/te').status_code == 200
        assert client.get('/admin').status_code == 302

        public_booking = client.post(
            '/book-appointment-public',
            data={
                'first_name': 'Public',
                'last_name': 'Booking',
                'email': 'public.booking@example.test',
                'phone': '9876543210',
                'date_of_birth': '1990-01-01',
                'gender': 'Female',
                'preferred_date': '2030-01-15',
                'preferred_time': '10:00',
                'problem_description': 'Routine checkup',
                'department': 'General Medicine',
            },
            follow_redirects=False,
        )
        assert public_booking.status_code == 302
        assert AppointmentRequest.query.filter_by(source='landing_page').count() == 1

        login_staff(client, 'ADM0001', 'admin123')
        assert client.get('/dashboard', follow_redirects=True).status_code == 200
        for path in ('/admin', '/admin/users', '/admin/patients', '/admin/appointments',
                     '/admin/wards', '/admin/assign-staff', '/doctor', '/nurse', '/shifts'):
            assert client.get(path).status_code == 200

        risk_response = client.get(f'/api/risk-analysis/{patient.id}')
        risk_data = risk_response.get_json()
        assert risk_response.status_code == 200
        assert risk_data['model_training_samples'] == 60
        assert 0 <= risk_data['model_risk_probability'] <= 1
        assert risk_data['vital_count'] >= 3
        assert risk_data['analyzed_at']
        assert client.get('/api/risk-analysis/all').status_code == 200
        risk_page = client.get(f'/patient/{patient.id}/risk-analysis')
        assert risk_page.status_code == 200
        assert b'Demo model signal' in risk_page.data
        assert b'Based on ' in risk_page.data

        created_shift = client.post(
            '/shifts/create',
            data={
                'staff_id': doctor.id,
                'shift_type': 'morning',
                'department': doctor.department or 'General Medicine',
                'date': '2030-01-15',
            },
            follow_redirects=False,
        )
        assert created_shift.status_code == 302


def test_risk_analysis_handles_insufficient_vitals(client):
    with app.app_context():
        patient = first_active_patient()
        VitalSign.query.filter_by(patient_id=patient.id).delete()
        db.session.commit()
        login_staff(client, 'ADM0001', 'admin123')

        response = client.get(f'/api/risk-analysis/{patient.id}')
        analysis = response.get_json()
        assert response.status_code == 200
        assert analysis['risk_level'] == 'unknown'
        assert analysis['vital_count'] == 0
        assert analysis['analyzed_at']
        assert analysis['model_training_samples'] == 60
        assert client.get(
            f'/patient/{patient.id}/risk-analysis'
        ).status_code == 200


def test_staff_workflows_and_hardware_vitals(client):
    with app.app_context():
        patient = first_active_patient()
        medication = Medication.query.filter_by(patient_id=patient.id).first()
        nurse = StaffMember.query.filter_by(role='nurse').first()

        login_staff(client, 'DOC0001', 'doctor1')
        assert client.get('/doctor').status_code == 200
        assert client.get(f'/patient/{patient.id}').status_code == 200
        assert client.get(f'/patient/{patient.id}/history').status_code == 200
        assert client.get(f'/medication/{patient.id}/schedule').status_code == 200
        assert client.post(
            '/doctor/schedule-round',
            data={'patient_id': patient.id, 'scheduled_time': '09:30', 'notes': 'Daily review'},
            follow_redirects=False,
        ).status_code == 302
        assert Round.query.count() == 1
        assert client.post(
            f'/doctor/note/{patient.id}/add',
            data={
                'note_type': 'progress',
                'subjective': 'Feeling better',
                'objective': 'Vitals reviewed',
                'assessment': 'Stable',
                'plan': 'Continue observation',
            },
            follow_redirects=False,
        ).status_code == 302
        assert DoctorNote.query.count() == 1
        assert client.post(
            f'/medication/{patient.id}/schedule',
            data={
                'name': 'Test Medication',
                'dosage': '10 mg',
                'frequency': 'Once daily',
                'route': 'oral',
            },
            follow_redirects=False,
        ).status_code == 302
        assert client.get('/handoff/create').status_code == 200
        assert client.post(
            f'/patient/{patient.id}/add-treatment',
            data={'treatment_type': 'Observation', 'description': 'Observed for 15 minutes'},
            follow_redirects=False,
        ).status_code == 302

        client.get('/logout')
        login_staff(client, 'NRS0001', 'nurse1')
        assert client.get('/nurse').status_code == 200
        assert client.get(f'/nurse/patient-lookup?patient_id={patient.patient_id}').status_code == 200
        updated = client.post(
            f'/nurse/update-patient/{patient.id}',
            data={'room_number': 'TEST-1', 'new_medications': '[]'},
        )
        assert updated.status_code == 200
        assert updated.get_json()['success'] is True
        assert client.post(
            f'/medication/{medication.id}/administer',
            data={'status': 'administered', 'notes': 'Test administration'},
            follow_redirects=False,
        ).status_code == 302
        assert client.get('/api/nurse/notifications').status_code == 200

        # Exercise the name-based Arduino fallback rather than relying on a
        # production-specific patient ID.
        bridge_patient = Patient(
            patient_id='ARDUINO-DEMO',
            first_name='Pavanth',
            last_name='Kumar Chopparapu',
            date_of_birth=patient.date_of_birth,
            gender='Male',
            status='admitted',
        )
        db.session.add(bridge_patient)
        db.session.commit()
        response = client.post('/api/arduino/vitals', json={'bpm': 72, 'spo2': 98, 'temp': 98.6})
        assert response.status_code == 200
        assert VitalSign.query.filter_by(patient_id=bridge_patient.id).count() == 1
        assert nurse is not None


def test_patient_portal_and_chat_fallback(client):
    with app.app_context():
        patient = first_active_patient()
        assert patient.phone

        portal_login = client.post(
            '/patient-login',
            data={'patient_id': patient.patient_id, 'phone': patient.phone},
            follow_redirects=False,
        )
        assert portal_login.status_code == 302
        for path in ('/patient-portal/dashboard', '/patient-portal/history',
                     '/patient-portal/medications', '/patient-portal/appointments',
                     '/patient-portal/chat'):
            assert client.get(path).status_code == 200

        chat_response = client.post(
            f'/api/patient/{patient.id}/chat',
            json={'message': 'What medications am I taking?', 'language': 'en'},
        )
        chat_data = chat_response.get_json()
        assert chat_response.status_code == 200
        assert chat_data['role'] == 'assistant'
        assert client.post(
            f"/api/chat/feedback/{chat_data['message_id']}",
            json={'is_helpful': True, 'feedback_text': 'Useful'},
        ).status_code == 200

        appointment = client.post(
            f'/api/patient/{patient.id}/book-appointment',
            json={
                'preferred_date': '2030-02-01T00:00:00',
                'preferred_time': '11:30',
                'appointment_type': 'routine',
                'department': 'General Medicine',
                'urgency': 'normal',
            },
        )
        assert appointment.status_code == 200
        assert appointment.get_json()['ok'] is True
        assert client.get('/api/discharged/summary').status_code == 200
        assert client.post(f'/api/patient/{patient.id}/clear-history').get_json()['ok'] is True


def test_patient_chat_uses_groq_chat_messages(client, monkeypatch):
    import routes

    with app.app_context():
        patient = first_active_patient()
        client.post(
            '/patient-login',
            data={'patient_id': patient.patient_id, 'phone': patient.phone},
        )

        captured = {}

        def fake_groq_response(messages):
            captured['messages'] = messages
            return 'Please contact your care team for medication guidance.'

        monkeypatch.setattr(routes, 'generate_ai_response', fake_groq_response)
        response = client.post(
            f'/api/patient/{patient.id}/chat',
            json={'message': 'What medications am I taking?', 'language': 'en'},
        )

        assert response.status_code == 200
        assert response.get_json()['message'].startswith('Please contact')
        assert captured['messages'][0]['role'] == 'system'
        assert captured['messages'][-1] == {
            'role': 'user',
            'content': 'What medications am I taking?',
        }


def test_groq_response_uses_configured_model(monkeypatch):
    from types import SimpleNamespace

    import app as app_module

    captured = {}

    class FakeCompletions:
        def create(self, *, model, messages):
            captured['model'] = model
            captured['messages'] = messages
            return SimpleNamespace(choices=[
                SimpleNamespace(message=SimpleNamespace(content='Groq response'))
            ])

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions()))
    monkeypatch.setattr(app_module, 'groq_client', fake_client)
    monkeypatch.setitem(app_module.app.config, 'AI_MODEL_NAME', 'test-model')

    messages = [{'role': 'user', 'content': 'hello'}]
    assert app_module.generate_ai_response(messages) == 'Groq response'
    assert captured == {'model': 'test-model', 'messages': messages}


def test_alert_voice_is_sent_only_to_open_assigned_dashboard_and_ack_stops_it(
        client, monkeypatch):
    import routes
    import routing

    with app.app_context():
        patient = Patient.query.filter(
            Patient.assigned_doctor_id.isnot(None),
            Patient.assigned_nurse_id.isnot(None),
        ).first()
        assert patient is not None
        doctor = patient.assigned_doctor
        nurse = patient.assigned_nurse
        assert doctor is not None
        assert nurse is not None
        doctor.set_password('voice-test')
        db.session.commit()

        alert = routing.create_patient_alert(
            patient,
            None,
            'critical',
            'Critical patient alert',
            'Critical vital reading',
        )
        db.session.commit()

        class FakeSocket:
            def __init__(self):
                self.events = []

            def emit(self, event, payload, to):
                self.events.append((event, payload, to))

        fake_socket = FakeSocket()
        monkeypatch.setattr(routing, '_socketio', lambda: fake_socket)
        routing.mark_dashboard_closed(doctor.id)
        routing.mark_dashboard_closed(nurse.id)
        routing.mark_dashboard_open(doctor.id)
        try:
            routing._publish_alert(
                alert, patient, routing.alert_recipients(patient))
            assert [event[2] for event in fake_socket.events] == [
                f'staff_{doctor.id}',
            ]

            login_staff(client, doctor.staff_id, 'voice-test')
            monkeypatch.setattr(routes, 'socketio', fake_socket)
            response = client.post(
                f'/alert/{alert.id}/acknowledge',
                headers={'X-Requested-With': 'XMLHttpRequest'},
            )

            assert response.status_code == 200
            assert alert.is_acknowledged is True
            acknowledged = [
                event for event in fake_socket.events
                if event[0] == 'alert_acknowledged'
            ]
            assert {event[2] for event in acknowledged} == {
                f'staff_{doctor.id}', f'staff_{nurse.id}',
            }
        finally:
            routing.mark_dashboard_closed(doctor.id)
            routing.mark_dashboard_closed(nurse.id)
