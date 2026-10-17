"""CareSync AI - support services.

Consolidates the previous ``synthetic_data.py``, ``twilio_utils.py``,
``emergency_announcer.py``, ``appointment_routing.py`` and
``predictive_analytics.py`` into one module.  Everything here is a *service*:
data generation, outbound messaging, appointments and risk analytics.

Layout
------
1. Configuration constants (thresholds, translations, demo catalogues)
2. Outbound messaging: SMS / e-mail, text-to-speech, n8n webhook
3. Vitals generation and clinical thresholds
4. Appointment routing engine
5. Predictive risk analytics
6. Demo data seeding
"""

import csv
import json
import logging
import os
import queue
import re
import threading
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from models import (
    Alert,
    DoctorNote,
    MedicineDetail,
    Medication,
    Patient,
    Round,
    Shift,
    StaffMember,
    VitalSign,
    db,
)

try:                                                   # optional dependency
    from dotenv import load_dotenv
    load_dotenv()
except Exception:                                      # pragma: no cover
    pass

log = logging.getLogger(__name__)


# ===========================================================================
# 1. Configuration
# ===========================================================================
#: model field -> (low, high, unit).  Shared by the simulator, the Arduino
#: endpoint and the monitor so every entry point grades vitals identically.
VITAL_RANGES = {
    'heart_rate': (60, 100, ' bpm'),
    'blood_pressure_systolic': (90, 140, ' mmHg'),
    'blood_pressure_diastolic': (60, 90, ' mmHg'),
    'oxygen_saturation': (94, 100, '%'),
    'temperature': (96.5, 99.5, '\u00b0F'),
    'respiratory_rate': (12, 20, ' /min'),
}

DEPARTMENTS = ["Emergency", "ICU", "Cardiology", "Neurology", "Pediatrics",
               "Oncology", "Orthopedics", "Surgery"]
SPECIALIZATIONS = ["General Medicine", "Cardiology", "Neurology", "Pediatrics",
                   "Emergency Medicine", "Surgery", "Oncology", "Orthopedics"]

DEMO_ADMIN_PASSWORD = os.environ.get('DEMO_ADMIN_PASSWORD', 'admin123')
DEMO_STAFF_PASSWORD = os.environ.get('DEMO_STAFF_PASSWORD', 'password123')



# ===========================================================================
# 2. Outbound messaging
# ===========================================================================
def format_to_e164(phone, default_prefix="+91"):
    """Normalise a phone number to E.164, defaulting to India (+91)."""
    if not phone:
        return ""
    cleaned = "".join(c for c in str(phone) if c.isdigit() or c == '+')
    if not cleaned:
        return ""
    if cleaned.startswith('+'):
        return cleaned
    if cleaned.startswith('0'):
        cleaned = cleaned[1:]
    return f"{default_prefix}{cleaned}"


class MessagingService:
    """SMS / e-mail delivery with a safe mock when credentials are absent.

    Nothing in the app hard-fails because a third-party key is missing: if
    Twilio is not configured the message is logged instead of sent, so demos
    and offline runs behave identically.
    """

    def __init__(self):
        self.dev_mode = os.environ.get('DEVELOPMENT_MODE', 'true').lower() == 'true'
        self.sid = os.environ.get('TWILIO_ACCOUNT_SID')
        self.token = os.environ.get('TWILIO_AUTH_TOKEN')
        self.from_number = os.environ.get('TWILIO_PHONE_NUMBER')
        self.enabled = bool(self.sid and self.token and self.from_number)
        self._client = None
        if not self.enabled:
            log.info("Twilio not configured - SMS will be logged, not sent")

    @property
    def client(self):
        if self._client is None and self.enabled:
            try:
                from twilio.rest import Client
                self._client = Client(self.sid, self.token)
            except Exception as exc:                   # pragma: no cover
                log.error("Twilio client init failed: %s", exc)
                self.enabled = False
        return self._client

    def send_sms(self, to_number, body):
        """Send an SMS; True when handed off (or mocked) successfully."""
        recipient = format_to_e164(to_number)
        if not recipient:
            return False
        if not self.enabled or self.client is None:
            log.info("[SMS MOCK] -> %s: %s", recipient, body)
            return True
        try:
            message = self.client.messages.create(
                body=body, from_=self.from_number, to=recipient)
            log.info("SMS sent to %s (sid=%s)", recipient, message.sid)
            return True
        except Exception as exc:
            # 21608 = recipient not verified; a soft success so the clinical
            # flow is never blocked by an unverified demo number.
            log.warning("SMS to %s failed: %s", recipient, exc)
            return '21608' in str(exc)

    def send_email(self, address, subject, body):
        """Best-effort e-mail.  Flask-Mail is optional; logs on absence."""
        if not address:
            return False
        try:
            from flask import current_app
            from flask_mail import Mail, Message
            Mail(current_app).send(
                Message(subject=subject, recipients=[address], body=body))
            log.info("Email sent to %s", address)
            return True
        except Exception as exc:
            log.info("[EMAIL MOCK] To: %s | %s (no mail server: %s)",
                     address, subject, type(exc).__name__)
            return self.dev_mode




messaging = MessagingService()


def send_vitals_alert_sms(patient_name, alert_title, alert_message, to_number=None):
    """Sends an SMS alert using Twilio / messaging service."""
    if not to_number:
        to_number = os.environ.get('ALERT_PHONE_NUMBER', "+919515104279")
    message_body = f"CareSync AI ALERT: {alert_title}\n{alert_message}"
    return messaging.send_sms(to_number, message_body)


class EmergencyAnnouncer:
    """Text-to-speech announcer running on a dedicated worker thread.

    The TTS engine is created *inside* the worker because the Windows SAPI
    engine is not safe to instantiate or drive from arbitrary threads.
    """

    def __init__(self):
        self.queue = queue.Queue()
        self.is_running = True
        self.worker = threading.Thread(
            target=self._run, daemon=True, name='caresync-tts')
        self.worker.start()

    def _run(self):
        engine = None
        try:
            import pyttsx3
            engine = pyttsx3.init()
            for voice in engine.getProperty('voices') or []:
                if 'female' in voice.name.lower() or 'zira' in voice.name.lower():
                    engine.setProperty('voice', voice.id)
                    break
            engine.setProperty('rate', 160)
            engine.setProperty('volume', 1.0)
        except Exception as exc:
            log.info("TTS unavailable (%s) - announcements will be skipped", exc)
            engine = None

        while self.is_running:
            item = self.queue.get()
            if item is None:
                break
            if engine is not None and item.get('text'):
                try:
                    engine.say(item['text'])
                    engine.runAndWait()
                    time.sleep(0.4)
                except Exception as exc:               # pragma: no cover
                    log.error("TTS playback failed: %s", exc)
            self.queue.task_done()

    def announce(self, text, severity='critical'):
        if text:
            self.queue.put({'text': text, 'severity': severity})
        return True

    def announce_emergency(self, patient_name, condition, room_number,
                           bed_number=None, severity='critical'):
        bed = f", bed {bed_number}" if bed_number else ""
        return self.announce(
            f"EMERGENCY ALERT! Patient {patient_name} in room {room_number}"
            f"{bed}. {condition}. Immediate attention required!", severity)

    def announce_vital_alert(self, patient, vital_type, value, unit,
                             room_number, bed_number=None):
        return self.announce_emergency(
            patient.full_name,
            f"Abnormal {str(vital_type).replace('_', ' ')} of {value}{unit}",
            room_number, bed_number)

    def shutdown(self):
        self.is_running = False
        self.queue.put(None)


announcer = EmergencyAnnouncer()


def announce_vital_emergency(patient, condition, room_number, bed_number=None):
    """Announce only when an assigned doctor or nurse dashboard is open."""
    from routing import alert_recipients, open_dashboards

    open_staff_ids = set(open_dashboards())
    if not any(staff.id in open_staff_ids
               for staff in alert_recipients(patient)):
        return False

    return announcer.announce_emergency(
        patient.full_name, condition, room_number, bed_number)


def send_to_n8n_webhook(alert):
    """POST an alert to n8n when ``N8N_WEBHOOK_URL`` is configured."""
    url = os.environ.get('N8N_WEBHOOK_URL')
    if not url:
        return False
    payload = {
        'alert_id': alert.id,
        'patient_id': alert.patient_id,
        'title': alert.title,
        'message': alert.message,
        'severity': alert.severity,
        'created_at': alert.created_at.isoformat() if alert.created_at else None,
    }
    try:
        request = urllib.request.Request(
            url, data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'}, method='POST')
        with urllib.request.urlopen(request, timeout=5) as response:
            log.info('Posted alert %s to n8n (status %s)',
                     alert.id, response.status)
        return True
    except Exception as exc:
        log.error('n8n webhook failed for alert %s: %s', alert.id, exc)
        return False


# ===========================================================================
# 3. Vitals generation
# ===========================================================================
def generate_vital_sign(patient, status_bias=None):
    """Produce a plausible vitals reading for ``patient``.

    ``status_bias`` of 'critical' / 'warning' / ``None`` steers the reading so
    ICU and emergency patients deteriorate more often than ward patients.
    """
    import random

    if status_bias == 'critical' or (patient.status in ('icu', 'emergency')
                                     and random.random() < 0.3):
        heart_rate = random.choice([random.randint(35, 50), random.randint(120, 160)])
        systolic = random.choice([random.randint(70, 85), random.randint(180, 210)])
        diastolic = random.choice([random.randint(40, 55), random.randint(110, 130)])
        oxygen = random.randint(80, 89)
        temperature = random.choice([random.uniform(95.0, 96.4),
                                     random.uniform(99.6, 103.0)])
        respiratory = random.choice([random.randint(6, 10), random.randint(28, 40)])
        status = 'critical'
    elif status_bias == 'warning' or random.random() < 0.15:
        heart_rate = random.choice([random.randint(52, 59), random.randint(101, 118)])
        systolic = random.choice([random.randint(88, 90), random.randint(141, 160)])
        diastolic = random.choice([random.randint(56, 59), random.randint(91, 100)])
        oxygen = random.randint(90, 93)
        temperature = random.choice([random.uniform(96.0, 96.4),
                                     random.uniform(99.6, 101.0)])
        respiratory = random.choice([random.randint(10, 11), random.randint(21, 25)])
        status = 'warning'
    else:
        heart_rate = random.randint(62, 98)
        systolic = random.randint(95, 138)
        diastolic = random.randint(62, 88)
        oxygen = random.randint(95, 100)
        temperature = random.uniform(96.6, 99.4)
        respiratory = random.randint(12, 20)
        status = 'normal'

    return VitalSign(
        patient_id=patient.id,
        heart_rate=round(heart_rate, 1),
        blood_pressure_systolic=systolic,
        blood_pressure_diastolic=diastolic,
        oxygen_saturation=round(float(oxygen), 1),
        temperature=round(temperature, 1),
        respiratory_rate=respiratory,
        status=status,
        recorded_at=datetime.now(),
    )


def create_initial_vitals(patients, readings=5):
    """Back-fill a short vitals history so charts are not empty on first load."""
    for patient in patients:
        for index in range(readings):
            vital = generate_vital_sign(patient)
            vital.recorded_at = datetime.now() - timedelta(minutes=index * 15)
            db.session.add(vital)
    db.session.commit()


# ===========================================================================
# 4. Demo data seeding
# ===========================================================================
FIRST_NAMES = [
    "Aarav", "Vivaan", "Aditya", "Arjun", "Sai", "Rohan", "Rahul", "Vikas",
    "Amit", "Siddharth", "Priya", "Sanya", "Ananya", "Sneha", "Pooja", "Kavya",
    "Suman", "Deepa", "Lakshmi", "Meera", "Suresh", "Ramesh", "Rajesh", "Sunil",
    "Kumar", "Venkatesh", "Manish", "Pavan", "Nisha", "Divya", "Bhavya", "Keerthi",
    "Shreya", "Ishaan", "Tara", "Kiran", "Ila", "Nitin", "Siddhi", "Ritika",
]

LAST_NAMES = [
    "Sharma", "Patel", "Singh", "Kumar", "Gupta", "Reddy", "Khan", "Mehta",
    "Nair", "Iyer", "Joshi", "Desai", "Bose", "Kapoor", "Chaudhary", "Saxena",
    "Malhotra", "Mishra", "Verma", "Shah", "Rao", "Ghosh", "Pillai", "Chowdhury",
    "Tripathi", "Prasad", "Naik", "Dutta", "Bhandari", "Nambiar",
]

DIAGNOSES = [
    "Acute myocardial infarction", "Pneumonia", "Stroke", "Diabetes mellitus",
    "Chronic kidney disease", "Heart failure", "COPD exacerbation", "Sepsis",
    "Acute appendicitis", "Fracture", "Hypertension crisis", "Asthma exacerbation",
    "Gastrointestinal bleeding", "Deep vein thrombosis", "Pulmonary embolism",
]

MEDICATIONS_LIST = [
    ("Aspirin", "81mg", "Once daily", "oral"),
    ("Metoprolol", "50mg", "Twice daily", "oral"),
    ("Lisinopril", "10mg", "Once daily", "oral"),
    ("Metformin", "500mg", "Once daily, after meals", "oral"),
    ("Atorvastatin", "40mg", "Once daily", "oral"),
    ("Omeprazole", "20mg", "Once daily, before breakfast", "oral"),
    ("Amlodipine", "5mg", "Once daily", "oral"),
    ("Ceftriaxone", "1g", "Twice daily", "IV"),
    ("Vancomycin", "1g", "Twice daily", "IV"),
    ("Morphine", "2mg", "As needed", "IV"),
    ("Heparin", "5000 units", "Three times daily", "subcutaneous"),
    ("Insulin Regular", "10 units", "Three times daily", "subcutaneous"),
]

#: Frequency label -> hours between doses, shared by every prescribing screen.
DOSAGE_INTERVAL_HOURS = {
    'Once daily': 24,
    'Twice daily': 12,
    'Three times daily': 8,
    'Four times daily': 6,
    'Every 4 hours': 4,
    'Every 6 hours': 6,
    'Every 8 hours': 8,
    'Every 12 hours': 12,
    'As needed': 0,
}

MEDICINE_DETAILS = [
    ("Paracetamol", "Fever and mild to moderate pain relief.",
     "Adults: 500mg-1g every 4-6 hours. Max 4g per day.",
     "Overdose harms the liver and kidneys. Avoid alcohol."),
    ("Aspirin", "Pain, fever and inflammation relief; also a blood thinner.",
     "Pain/fever: 325-650mg every 4 hours. Cardiac: 81mg daily.",
     "Can cause stomach bleeding. Not for children with viral infections."),
    ("Ibuprofen", "NSAID for pain, fever and inflammation.",
     "200-400mg every 4-6 hours with food.",
     "Long-term use affects kidneys, heart and stomach."),
    ("Metoprolol", "Beta-blocker for hypertension, angina and heart failure.",
     "25-100mg once or twice daily as prescribed.",
     "Do not stop abruptly - may cause rebound tachycardia."),
    ("Metformin", "Controls blood sugar in type 2 diabetes.",
     "500-1000mg with meals.",
     "Take with food. Rare risk of lactic acidosis."),
    ("Atorvastatin", "Lowers cholesterol and cardiovascular risk.",
     "10-80mg daily, usually in the evening.",
     "Report unexplained muscle pain. Limit grapefruit juice."),
    ("Omeprazole", "Proton-pump inhibitor for ulcers, reflux and GERD.",
     "20-40mg daily, 30-60 minutes before a meal.",
     "Long-term use may lower magnesium levels."),
    ("Amlodipine", "Calcium channel blocker for hypertension and chest pain.",
     "5-10mg daily.",
     "May cause ankle swelling or flushing."),
    ("Ceftriaxone", "Antibiotic for a wide range of bacterial infections.",
     "1-2g daily by IV or IM injection.",
     "Tell staff about any penicillin allergy."),
    ("Vancomycin", "Antibiotic for serious infections including MRSA.",
     "Dosed by weight and kidney function, given IV.",
     "Needs blood-level monitoring. Infuse slowly (red-man syndrome)."),
    ("Morphine", "Opioid analgesic for moderate to severe pain.",
     "2-10mg IV as needed for breakthrough pain.",
     "Causes drowsiness and slowed breathing. Dependence risk."),
    ("Heparin", "Anticoagulant that prevents and treats blood clots.",
     "Given by injection or continuous IV infusion.",
     "Increases bleeding risk; monitor aPTT closely."),
    ("Insulin Regular", "Fast-acting insulin for blood-sugar control.",
     "Subcutaneous injection before meals.",
     "Monitor blood sugar closely to avoid hypoglycaemia."),
]


def populate_medicine_details():
    """Seed the drug-information reference used by the patient portal."""
    added = 0
    for name, usage, dosage, precautions in MEDICINE_DETAILS:
        if not MedicineDetail.query.filter_by(name=name).first():
            db.session.add(MedicineDetail(
                name=name, usage=usage, dosage=dosage, precautions=precautions))
            added += 1
    db.session.commit()
    return added


def next_staff_id(role):
    """Allocate the next unused ``DOC####`` / ``NRS####`` identifier."""
    prefix = {'doctor': 'DOC', 'nurse': 'NRS', 'admin': 'ADM'}.get(role, 'STF')
    highest = 0
    for staff in StaffMember.query.filter(StaffMember.staff_id.like(f'{prefix}%')).all():
        match = re.search(r'(\d+)$', staff.staff_id)
        if match:
            highest = max(highest, int(match.group(1)))
    return f'{prefix}{highest + 1:04d}'


def create_admin():
    """Create the single administrator account if it is missing."""
    existing = StaffMember.query.filter_by(role='admin').first()
    if existing:
        return existing
    admin = StaffMember(
        staff_id='ADM0001', first_name='Pavanth', last_name='Kumar',
        email='admin@caresync.hospital', phone='9700443157', role='admin',
        department='Administration', is_on_duty=True, is_active=True)
    admin.set_password(DEMO_ADMIN_PASSWORD)
    db.session.add(admin)
    db.session.commit()
    log.info("Created admin %s", admin.staff_id)
    return admin


def create_staff(num_doctors=25, num_nurses=40, password=None):
    """Create demo doctors and nurses, all on duty so routing has candidates."""
    import random
    password = password or DEMO_STAFF_PASSWORD
    created = []

    for index in range(num_doctors):
        staff_id = next_staff_id('doctor')
        specialization = SPECIALIZATIONS[index % len(SPECIALIZATIONS)]
        doctor = StaffMember(
            staff_id=staff_id,
            first_name=FIRST_NAMES[index % len(FIRST_NAMES)],
            last_name=LAST_NAMES[index % len(LAST_NAMES)],
            email=f"{staff_id.lower()}@caresync.hospital",
            phone=f"555-{random.randint(100, 999)}-{random.randint(1000, 9999)}",
            role='doctor',
            department=specialization,
            specialization=specialization,
            is_on_duty=True,
            is_active=True,
        )
        doctor.set_password(password)
        db.session.add(doctor)
        created.append(doctor)

    for index in range(num_nurses):
        staff_id = next_staff_id('nurse')


def create_patients(num_patients=60, num_discharged=15):
    """Create demo patients and let the routing engine assign their care team.

    Note that doctor/nurse assignment is *not* done here: patients are created
    unassigned and then handed to ``routing.PatientRouter``, so the same
    balancing rules apply to seeded data as to live admissions.
    """
    import random

    created = []
    existing = Patient.query.count()
    for index in range(num_patients):
        number = existing + index + 1
        status = random.choices(
            ['admitted', 'icu', 'emergency', 'discharged'],
            weights=[55, 15, 10, 20])[0]
        birth_year = datetime.now().year - random.randint(18, 85)
        patient = Patient(
            patient_id=f'PAT{number:06d}',
            first_name=random.choice(FIRST_NAMES),
            last_name=random.choice(LAST_NAMES),
            date_of_birth=datetime(birth_year, random.randint(1, 12),
                                   random.randint(1, 28)).date(),
            gender=random.choice(['Male', 'Female']),
            blood_type=random.choice(['A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-']),
            phone=f"555-{random.randint(100, 999)}-{random.randint(1000, 9999)}",
            email=f"patient{number}@caresync.hospital",
            address=f"{random.randint(100, 9999)} "
                    f"{random.choice(['Main', 'Oak', 'Maple', 'Cedar'])} Street",
            emergency_contact_name=f"{random.choice(FIRST_NAMES)} "
                                   f"{random.choice(LAST_NAMES)}",
            emergency_contact_phone=f"555-{random.randint(100, 999)}-"
                                    f"{random.randint(1000, 9999)}",
            room_number=f"{random.choice('ABCD')}{random.randint(101, 450)}",
            bed_number=str(random.randint(1, 4)),
            status=status,
            admission_date=datetime.now() - timedelta(days=random.randint(0, 14)),
            diagnosis=random.choice(DIAGNOSES),
            department=random.choice(DEPARTMENTS) if status != 'discharged' else None,
        )
        if status == 'discharged':
            patient.discharge_date = datetime.now()
        db.session.add(patient)
        created.append(patient)

    db.session.commit()
    log.info("Created %d patients", len(created))
    return created


def create_medications_for_patients(patients):
    """Attach a plausible medication regimen to each seeded patient."""
    import random
    for patient in patients:
        for name, dosage, frequency, route in random.sample(
                MEDICATIONS_LIST, random.randint(1, 3)):
            db.session.add(Medication(
                patient_id=patient.id,
                name=name, dosage=dosage, frequency=frequency, route=route,
                start_date=patient.admission_date,
                next_due=datetime.now() + timedelta(hours=random.randint(1, 8)),
                is_active=True,
            ))
    db.session.commit()


def seed_wards():
    """Create a ward row for every department that does not have one yet."""
    from models import Ward

    added = 0
    existing = {ward.name for ward in Ward.query.all()}
    for index, name in enumerate(sorted(set(DEPARTMENTS) | existing)):
        if name in existing:
            continue
        head_nurse = StaffMember.query.filter_by(role='nurse', department=name).first()
        db.session.add(Ward(
            name=name, capacity=30, floor=f"{(index % 5) + 1}",
            head_nurse_id=head_nurse.id if head_nurse else None))
        added += 1
    db.session.commit()
    return added


def initialize_synthetic_data(num_doctors=25, num_nurses=40,
                              num_patients=60, num_discharged=15):
    """Populate a fresh database with a full, balanced demo ward.

    The final step hands every patient to the routing engine, so the seeded
    data satisfies the same "every doctor has at least two patients" and
    "every patient has a doctor and a nurse" rules as live traffic.
    """
    from routing import router

    create_admin()
    create_staff(num_doctors=num_doctors, num_nurses=num_nurses)

    patients = create_patients(num_patients=num_patients,
                               num_discharged=num_discharged)
    if patients:
        create_initial_vitals(patients)
        create_medications_for_patients(patients)

    populate_medicine_details()
    seed_wards()

    routed = router.route_unassigned_patients()
    rebalanced = router.rebalance_workload()
    log.info("Seed complete: %d patients routed, %d rebalanced",
             routed, rebalanced)
    return True



# ===========================================================================
# 5. Appointment routing
# ===========================================================================
#: Localised labels for the appointment form.  Kept compact - only the fields
#: the UI actually renders are translated.
APPOINTMENT_TRANSLATIONS = {
    'en': {
        'appointment_types': {
            'consultation': 'General Consultation', 'follow-up': 'Follow-up Visit',
            'emergency': 'Emergency Appointment', 'routine': 'Routine Checkup',
            'diagnostic': 'Diagnostic Test'},
        'departments': {
            'General': 'General Medicine', 'Cardiology': 'Cardiology',
            'Neurology': 'Neurology', 'Surgery': 'General Surgery',
            'ICU': 'Intensive Care Unit', 'Emergency': 'Emergency Department',
            'Pediatrics': 'Pediatrics', 'Orthopedics': 'Orthopedics'},
        'urgency_levels': {'normal': 'Normal', 'urgent': 'Urgent',
                           'emergency': 'Emergency'},
    },
    'hi': {
        'appointment_types': {
            'consultation': 'सामान्य परामर्श', 'follow-up': 'अनुवर्ती दौरा',
            'emergency': 'आपातकालीन नियुक्ति', 'routine': 'नियमित जांच',
            'diagnostic': 'नैदानिक परीक्षण'},
        'departments': {
            'General': 'सामान्य चिकित्सा', 'Cardiology': 'कार्डियोलॉजी',
            'Neurology': 'न्यूरोलॉजी', 'Surgery': 'सामान्य शल्य चिकित्सा',
            'ICU': 'गहन चिकित्सा इकाई', 'Emergency': 'आपातकालीन विभाग',
            'Pediatrics': 'बाल रोग', 'Orthopedics': 'हड्डी रोग'},
        'urgency_levels': {'normal': 'सामान्य', 'urgent': 'तत्काल',
                           'emergency': 'आपातकाल'},
    },
    'te': {
        'appointment_types': {
            'consultation': 'సాధారణ సలహా', 'follow-up': 'ఫాలో-అప్ సందర్శన',
            'emergency': 'ఎమర్జెన్సీ నిర్ధారణ', 'routine': 'రూటిన్ చెక్-అప్',
            'diagnostic': 'రోగ నిర్ధారణ పరీక్ష'},
        'departments': {
            'General': 'సాధారణ వైద్యం', 'Cardiology': 'హృదయ శాస్త్రం',
            'Neurology': 'నాడీ శాస్త్రం', 'Surgery': 'సాధారణ శస్త్ర చికిత్స',
            'ICU': 'తీవ్ర పరిశ్రమ', 'Emergency': 'అత్యవసర శాఖ',
            'Pediatrics': 'శిశువ శాస్త్రం', 'Orthopedics': 'మూడగుర్ల శాస్త్రం'},
        'urgency_levels': {'normal': 'సాధారణం', 'urgent': 'తక్షణం',
                           'emergency': 'అత్యవసరం'},
    },
    'ta': {
        'appointment_types': {
            'consultation': 'பொதுவான ஆலோசனை', 'follow-up': 'பின்தொடர்ந்து வரவு',
            'emergency': 'அவசர நியமனம்', 'routine': 'வழக்கமான பரிசோதனை',
            'diagnostic': 'நோய் நிர்ணய சோதனை'},
        'departments': {
            'General': 'பொதுவான மருத்துவம்', 'Cardiology': 'இதய சிகிச்சை',
            'Neurology': 'நரம்பு மருத்துவம்', 'Surgery': 'பொதுவான அறுவை',
            'ICU': 'தீவிர சிகிச்சை', 'Emergency': 'அவசர பிரிவு',
            'Pediatrics': 'குழந்தை மருத்துவம்', 'Orthopedics': 'எலும்பு மருத்துவம்'},
        'urgency_levels': {'normal': 'சாதாரணம்', 'urgent': 'அவசரம்',
                           'emergency': 'அவசர நிலை'},
    },
    'ml': {
        'appointment_types': {
            'consultation': 'പൊതു ഉപദേശം', 'follow-up': 'പിന്തുടരുന്ന സന്ദർശനം',
            'emergency': 'അടിയന്തര നിയമനം', 'routine': 'വിപുല പരിശോധന',
            'diagnostic': 'രോഗ നിർണയ പരിശോധന'},
        'departments': {
            'General': 'പൊതു വൈദ്യം', 'Cardiology': 'ഹൃദയാശയം',
            'Neurology': 'നാഡിശാസ്ത്രം', 'Surgery': 'പൊതു ശസ്ത്രചികിത്സ',
            'ICU': 'ഗുരുതര പരിചരണം', 'Emergency': 'അടിയന്തര വിഭാഗം',
            'Pediatrics': 'കുട്ടികളുടെ ശാസ്ത്രം', 'Orthopedics': 'എല്ലോഴ ശസ്ത്രചികിത്സ'},
        'urgency_levels': {'normal': 'സാധാരണം', 'urgent': 'അടിയന്തരം',
                           'emergency': 'അടിയന്തര സ്ഥിതി'},
    },
}


def get_appointment_translations(language='en'):
    """Return UI labels for ``language``, falling back to English."""
    return APPOINTMENT_TRANSLATIONS.get(
        language, APPOINTMENT_TRANSLATIONS['en'])


class AppointmentRouter:
    """Scores candidate doctors and allocates an appointment to the best fit.

    The score blends speciality match, current workload, availability, existing
    doctor-patient history and urgency.  Ties break on staff id so the same
    request always routes the same way.
    """

    WEIGHTS = {
        'specialization': 0.30,
        'workload': 0.25,
        'availability': 0.20,
        'history': 0.15,
        'urgency': 0.10,
    }

    def _specialization_score(self, doctor, department, appointment_type):
        if not doctor.specialization or not department:
            return 0.5
        spec, dept = doctor.specialization.lower(), department.lower()
        if spec == dept:
            return 1.0
        if dept in spec or spec in dept:
            return 0.8
        if 'general' in spec and appointment_type in ('routine', 'consultation'):
            return 0.7
        return 0.3

    def _workload_score(self, doctor, preferred_date):
        if not preferred_date:
            return 0.5
        count = Patient.query.filter(
            Patient.assigned_doctor_id == doctor.id,
            Patient.status.in_(('admitted', 'icu', 'emergency')),
        ).count()
        return max(0.0, 1 - (count / 10.0))

    def _availability_score(self, doctor, preferred_date):
        if not preferred_date:
            return 0.5
        shift = Shift.query.filter(
            Shift.staff_id == doctor.id,
            db.func.date(Shift.start_time) == preferred_date.date(),
            Shift.is_active.is_(True),
        ).first()
        if shift:
            return 1.0
        # No shift data configured at all -> treat everyone as available.
        return 1.0 if Shift.query.count() == 0 else 0.6

    def _history_score(self, doctor, patient):
        if DoctorNote.query.filter_by(
                doctor_id=doctor.id, patient_id=patient.id).count() > 0:
            return 1.0
        if patient.assigned_doctor_id == doctor.id:
            return 0.9
        return 0.4

    def _urgency_score(self, doctor, urgency):
        if urgency == 'emergency':
            return 1.0 if doctor.is_on_duty else 0.5
        if urgency == 'urgent':
            return 0.9 if doctor.is_on_duty else 0.7
        return 0.8

    def score(self, doctor, appointment):
        patient = appointment.patient
        return (
            self._specialization_score(
                doctor, appointment.department, appointment.appointment_type
            ) * self.WEIGHTS['specialization']
            + self._workload_score(doctor, appointment.preferred_date)
            * self.WEIGHTS['workload']
            + self._availability_score(doctor, appointment.preferred_date)
            * self.WEIGHTS['availability']
            + self._history_score(doctor, patient) * self.WEIGHTS['history']
            + self._urgency_score(doctor, appointment.urgency)
            * self.WEIGHTS['urgency']
        )

    def candidates(self, appointment):
        """Doctors eligible for this appointment, best first."""
        department = appointment.department or 'General Medicine'
        doctors = StaffMember.query.filter(
            StaffMember.role == 'doctor',
            StaffMember.is_active.is_(True),
        ).all()
        matching = [d for d in doctors
                    if (d.department or '').lower() == department.lower()
                    or department.lower() in (d.specialization or '').lower()]
        pool = matching or doctors
        return sorted(pool, key=lambda d: (-self.score(d, appointment), d.id))

    def route_appointment(self, appointment):
        """Allocate ``appointment`` to a doctor and persist the decision."""
        try:
            ranked = self.candidates(appointment)
            if not ranked:
                log.warning("No doctor available for appointment %s",
                            appointment.id)
                return None
            best = ranked[0]
            appointment.doctor_id = best.id
            appointment.allocated_by_system = True
            appointment.routing_score = round(self.score(best, appointment), 4)
            if appointment.status == 'pending':
                appointment.status = 'confirmed'
            db.session.commit()
            log.info("Appointment %s routed to %s (score=%.3f)",
                     appointment.id, best.full_name, appointment.routing_score)
            return best
        except Exception as exc:
            db.session.rollback()
            log.error("Appointment allocation failed: %s", exc)
            return None

    def routing_details(self, appointment):
        """Full scoring breakdown, surfaced in the admin portal."""
        ranked = self.candidates(appointment)
        return {
            'appointment_id': appointment.id,
            'allocated_doctor_id': appointment.doctor_id,
            'routing_score': appointment.routing_score,
            'allocated_by_system': appointment.allocated_by_system,
            'candidates': [
                {'id': d.id, 'name': d.full_name,
                 'specialization': d.specialization,
                 'score': round(self.score(d, appointment), 4),
                 'workload': Patient.query.filter(
                     Patient.assigned_doctor_id == d.id,
                     Patient.status.in_(('admitted', 'icu', 'emergency')),
                 ).count(),
                 'on_duty': bool(d.is_on_duty)}
                for d in ranked[:10]
            ],
        }


appointment_router = AppointmentRouter()


def translate_text(text, source, target):
    """Best-effort translation; returns the input unchanged when unavailable.

    ``deep-translator`` calls an external service, so every failure mode is
    swallowed and the original text is returned.  The chat feature degrades to
    same-language replies rather than erroring.
    """
    if not text or source == target:
        return text
    try:
        from deep_translator import GoogleTranslator
        return GoogleTranslator(source=source, target=target).translate(text) or text
    except Exception as exc:
        log.info("Translation %s->%s unavailable (%s); using original text",
                 source, target, type(exc).__name__)
        return text


class AppointmentNotificationService:
    """Patient-facing messages for the appointment lifecycle.

    Wraps the shared :class:`MessagingService` so booking, confirmation and
    cancellation all go out the same way, and so an absent Twilio key degrades
    to a logged mock rather than an exception.
    """

    def __init__(self):
        self.dev_mode = messaging.dev_mode

    def send_booking_notification(self, appointment):
        patient = appointment.patient
        if not patient:
            return False
        message = (f"Dear {patient.first_name}, your CareSync request for "
                   f"{appointment.department} has been received and is being "
                   f"allocated to a doctor.")
        if patient.phone:
            messaging.send_sms(patient.phone, message)
        return True

    def send_appointment_confirmation(self, appointment, doctor):
        patient = appointment.patient
        if not patient or not doctor:
            return False
        when = appointment.preferred_date.strftime('%d-%m-%Y') \
            if appointment.preferred_date else 'the requested date'
        message = (f"Dear {patient.first_name}, your appointment in "
                   f"{appointment.department} with Dr. {doctor.last_name} is "
                   f"confirmed for {when} at {appointment.preferred_time}.")
        if patient.phone:
            messaging.send_sms(patient.phone, message)
        if patient.email:
            messaging.send_email(patient.email, 'Appointment confirmed', message)
        return True

    def send_admin_alert(self, phone, message):
        """Notify the hospital desk about a new public booking."""
        return messaging.send_sms(phone, message)

    def send_patient_sms(self, patient, message):
        if not patient or not patient.phone:
            return False
        return messaging.send_sms(patient.phone, message)


notification_service = AppointmentNotificationService()



# ===========================================================================
# 6. Predictive risk analytics
# ===========================================================================
#: Points added to the risk score for each abnormal average, and the
#: thresholds that map a score onto a level.
RISK_THRESHOLDS = ((70, 'critical'), (50, 'high'), (30, 'moderate'), (1, 'low'))


class RiskPredictor:
    """Rule-based deterioration risk scoring with an optional ML assist.

    The transparent threshold-and-trend logic is the clinical path.  A small
    scikit-learn model trained on the bundled synthetic CSV is applied purely
    as a supplementary signal; it never overrides the rule-based verdict.
    """

    def __init__(self):
        self.scaler = None
        self.anomaly_detector = None
        self.risk_classifier = None
        self.is_trained = False
        self.training_metadata = {}

    # -- optional model ---------------------------------------------------
    def train_from_csv(self, data_path):
        """Train the demo model from a labelled vitals CSV.

        The bundled CSV is synthetic and exists to exercise the code path.  It
        is not a clinically validated model.
        """
        import numpy as np
        from sklearn.ensemble import IsolationForest, RandomForestClassifier
        from sklearn.preprocessing import StandardScaler

        required = ('heart_rate', 'blood_pressure_systolic',
                    'blood_pressure_diastolic', 'oxygen_saturation',
                    'temperature', 'respiratory_rate', 'risk_label')
        data_path = Path(data_path)
        if not data_path.is_file():
            raise FileNotFoundError(f'Training data was not found: {data_path}')

        rows, labels = [], []
        with data_path.open(newline='', encoding='utf-8') as source:
            reader = csv.DictReader(source)
            missing = set(required) - set(reader.fieldnames or [])
            if missing:
                raise ValueError(
                    f'Training data is missing required columns: '
                    f"{', '.join(sorted(missing))}")
            for number, row in enumerate(reader, start=2):
                try:
                    values = [float(row[c]) for c in required[:-1]]
                    label = int(row['risk_label'])
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        f'Invalid training value on row {number}') from exc
                if label not in (0, 1):
                    raise ValueError(
                        f'Risk label on row {number} must be 0 or 1')
                if not all(np.isfinite(v) for v in values):
                    raise ValueError(f'Non-finite training value on row {number}')
                rows.append(values)
                labels.append(label)

        if len(rows) < 20:
            raise ValueError('At least 20 labeled observations are required')
        if len(set(labels)) != 2:
            raise ValueError('Training data must include both risk classes')

        features = np.asarray(rows, dtype=float)
        targets = np.asarray(labels, dtype=int)
        self.scaler = StandardScaler().fit(features)
        scaled = self.scaler.transform(features)
        self.anomaly_detector = IsolationForest(
            contamination=0.1, random_state=42).fit(scaled)
        self.risk_classifier = RandomForestClassifier(
            n_estimators=50, random_state=42).fit(scaled, targets)
        self.is_trained = True
        self.training_metadata = {
            'source': str(data_path),
            'sample_count': len(rows),
            'class_counts': {
                'stable': int(np.count_nonzero(targets == 0)),
                'high_risk': int(np.count_nonzero(targets == 1)),
            },
        }
        return self.training_metadata

    def _features(self, vitals):
        import numpy as np
        rows = [[
            v.heart_rate or 75,
            v.blood_pressure_systolic or 120,
            v.blood_pressure_diastolic or 80,
            v.oxygen_saturation or 98,
            v.temperature or 98.6,
            v.respiratory_rate or 16,
        ] for v in vitals]
        return np.asarray(rows, dtype=float) if rows else np.asarray([])

    @property
    def model_training_samples(self):
        return self.training_metadata.get('sample_count', 0) if self.training_metadata else 0

    def model_risk_probability(self, vital):
        """Supplementary model probability for one reading, or None."""
        if not self.is_trained:
            return None
        try:
            features = self._features([vital])
            if features.size == 0:
                return None
            scaled_features = self.scaler.transform(features)
            classes = list(self.risk_classifier.classes_)
            if 1 not in classes:
                return 0.0
            probability_index = classes.index(1)
            probability = self.risk_classifier.predict_proba(scaled_features)[0][probability_index]
            return round(float(probability), 3)
        except Exception as exc:
            log.error("Error computing model risk probability: %s", exc)
            return None

    @staticmethod
    def _trend(values, window=5):
        """Return the least-squares slope from oldest to newest readings."""
        import numpy as np

        recent = list(values[:window])[::-1]
        if len(recent) < 2:
            return 0.0
        return float(np.polyfit(np.arange(len(recent)), recent, 1)[0])

    @staticmethod
    def _direction(slope, tolerance):
        if slope > tolerance:
            return 'increasing'
        if slope < -tolerance:
            return 'decreasing'
        return 'stable'

    def analyze_patient_risk(self, patient_id):
        """Score a patient's deterioration risk from their recent vitals."""
        import numpy as np

        vitals = VitalSign.query.filter_by(patient_id=patient_id).order_by(
            VitalSign.recorded_at.desc()).limit(20).all()
        if len(vitals) < 3:
            assessed_at = datetime.now().isoformat()
            return {'risk_level': 'unknown', 'risk_score': 0,
                    'risk_factors': [], 'predictions': [],
                    'message': 'Insufficient data for analysis',
                    'patient_id': patient_id,
                    'vital_count': len(vitals),
                    'model_probability': None,
                    'model_risk_probability': 0.0,
                    'model_training_samples': self.model_training_samples,
                    'latest_vitals': None,
                    'assessed_at': assessed_at,
                    'analyzed_at': assessed_at}

        latest = vitals[0]
        score = 0
        factors = []

        def add(points, kind, severity, message, trend):
            nonlocal score
            score += points
            factors.append({'type': kind, 'severity': severity,
                            'message': message, 'trend': trend})

        # -- heart rate ---------------------------------------------------
        hr = [v.heart_rate for v in vitals if v.heart_rate]
        if hr:
            avg, slope = float(np.mean(hr)), self._trend(hr)
            trend = self._direction(slope, 0.5)
            if avg < 55 or avg > 110:
                add(25, 'heart_rate', 'high',
                    f'Average heart rate ({avg:.0f} bpm) is outside normal range',
                    trend)
            elif avg < 60 or avg > 100:
                add(10, 'heart_rate', 'medium',
                    f'Heart rate trending towards abnormal ({avg:.0f} bpm)', trend)
            if float(np.std(hr)) > 15:
                add(15, 'heart_rate_variability', 'medium',
                    f'High heart rate variability (SD {np.std(hr):.1f})', 'unstable')

        # -- blood pressure ----------------------------------------------
        systolic = [v.blood_pressure_systolic for v in vitals
                    if v.blood_pressure_systolic]
        if systolic:
            avg, slope = float(np.mean(systolic)), self._trend(systolic)
            trend = self._direction(slope, 1)
            if avg < 85 or avg > 165:
                add(30, 'blood_pressure', 'high',
                    f'Blood pressure ({avg:.0f} mmHg systolic) critically abnormal',
                    trend)
            elif avg < 95 or avg > 145:
                add(15, 'blood_pressure', 'medium',
                    'Blood pressure trending towards abnormal range', trend)

        # -- oxygen --------------------------------------------------------
        oxygen = [v.oxygen_saturation for v in vitals if v.oxygen_saturation]
        if oxygen:
            avg, slope = float(np.mean(oxygen)), self._trend(oxygen)
            trend = self._direction(slope, 0.2)
            if avg < 90:
                add(35, 'oxygen_saturation', 'critical',
                    f'Oxygen saturation critically low ({avg:.0f}%)', trend)

        # -- temperature ---------------------------------------------------
        temps = [v.temperature for v in vitals if v.temperature]
        if temps:
            avg, slope = float(np.mean(temps)), self._trend(temps)
            trend = self._direction(slope, 0.1)
            if avg < 96 or avg > 102:
                add(25, 'temperature', 'high',
                    f'Temperature critically abnormal ({avg:.1f}\u00b0F)', trend)
            elif avg < 97 or avg > 100:
                add(10, 'temperature', 'medium',
                    f'Temperature outside normal range ({avg:.1f}\u00b0F)', trend)

        # -- respiratory rate ----------------------------------------------
        resp = [v.respiratory_rate for v in vitals if v.respiratory_rate]
        if resp:
            avg = float(np.mean(resp))
            if avg < 10 or avg > 28:
                add(25, 'respiratory_rate', 'high',
                    f'Respiratory rate abnormal ({avg:.0f}/min)', 'abnormal')

        level = 'stable'
        for threshold, name in RISK_THRESHOLDS:
            if score >= threshold:
                level = name
                break

        predictions = []
        for factor in factors:
            if factor['trend'] == 'increasing':
                if factor['type'] == 'heart_rate':
                    predictions.append('Heart rate may reach critical levels in 1-2 hours')
                elif factor['type'] == 'blood_pressure':
                    predictions.append('Blood pressure trending upward - monitor closely')
                elif factor['type'] == 'temperature':
                    predictions.append('Fever may worsen - consider intervention')
            elif (factor['trend'] == 'decreasing'
                  and factor['type'] == 'oxygen_saturation'):
                predictions.append('Oxygen levels may need intervention within 30-60 minutes')

        model_probability = self.model_risk_probability(latest)
        assessed_at = datetime.now().isoformat()
        return {
            'patient_id': patient_id,
            'risk_level': level,
            'risk_score': score,
            'risk_factors': factors,
            'predictions': predictions,
            'model_probability': model_probability,
            'model_risk_probability': model_probability if model_probability is not None else 0.0,
            'model_training_samples': self.model_training_samples,
            'vital_count': len(vitals),
            'latest_vitals': {
                'heart_rate': latest.heart_rate,
                'blood_pressure': f"{latest.blood_pressure_systolic}/"
                                  f"{latest.blood_pressure_diastolic}",
                'oxygen_saturation': latest.oxygen_saturation,
                'temperature': latest.temperature,
            },
            'assessed_at': assessed_at,
            'analyzed_at': assessed_at,
        }


risk_predictor = RiskPredictor()


def train_demo_model():
    """Best-effort training of the bundled synthetic model."""
    data_path = Path(__file__).with_name('data') / 'demo_vitals_training.csv'
    try:
        metadata = risk_predictor.train_from_csv(data_path)
        log.info("Demo risk model trained from %s observations",
                 metadata['sample_count'])
        return metadata
    except Exception as exc:
        log.warning("Demo risk model not trained (%s); rule-based scoring "
                    "remains active", exc)
        return None


# Routes can be served by WSGI/Flask CLI as well as app.main(). Train the
# optional model when the risk service is loaded so every entry point is ready.
train_demo_model()




def create_predictive_alert(patient_id, analysis):
    """Raise or refresh a single predictive alert for a high-risk patient.

    Only 'high' and 'critical' risks raise an alert, and an existing open alert
    is refreshed in place rather than duplicated - both rules keep the alert
    count low.
    """
    from routing import alert_recipients, next_sequence_number, _publish_alert

    level = analysis.get('risk_level')
    if level not in ('high', 'critical'):
        return None

    patient = Patient.query.get(patient_id)
    if not patient:
        return None

    severity = 'critical' if level == 'critical' else 'warning'
    predictions = analysis.get('predictions') or ['Early intervention recommended']
    message = (f"Risk score {analysis.get('risk_score', 0)}. "
               + '; '.join(predictions))
    title = f"AI risk alert - {patient.full_name}"

    existing = Alert.query.filter_by(
        patient_id=patient_id, alert_type='predictive_warning',
        is_acknowledged=False).first()

    if existing:
        existing.message = message
        existing.severity = severity
        existing.created_at = datetime.now()
        existing.ack_deadline = datetime.now() + timedelta(minutes=2)
        db.session.commit()
        send_to_n8n_webhook(existing)
        return existing

    alert = Alert(
        patient_id=patient_id,
        alert_type='predictive_warning',
        severity=severity,
        title=title,
        message=message,
        ack_deadline=datetime.now() + timedelta(minutes=2),
        sequence_no=next_sequence_number(),
    )
    db.session.add(alert)
    db.session.flush()
    _publish_alert(alert, patient, alert_recipients(patient))
    db.session.commit()
    send_to_n8n_webhook(alert)

    return alert


def analyze_all_patients(create_alerts=True):
    """Risk-score every monitored patient, optionally raising alerts."""
    patients = Patient.query.filter(
        Patient.status.in_(('admitted', 'icu', 'emergency'))).all()
    results = []
    for patient in patients:
        try:
            analysis = risk_predictor.analyze_patient_risk(patient.id)
            results.append({
                'patient_id': patient.id,
                'patient_name': patient.full_name,
                'room': patient.room_number,
                **analysis,
            })
            if create_alerts and analysis['risk_level'] in ('high', 'critical'):
                create_predictive_alert(patient.id, analysis)
        except Exception as exc:
            log.error("Risk analysis failed for patient %s: %s", patient.id, exc)
    return results
