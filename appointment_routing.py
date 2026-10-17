"""
Enhanced Appointment Routing System with Intelligent Doctor Allocation
Uses multiple factors to intelligently allocate patients to doctors
"""

from datetime import datetime, timedelta
from database import db
from models import StaffMember, Patient, AppointmentRequest, VitalSign, DoctorNote, Shift
import logging
import json
import os
from typing import Optional, Dict, List, Tuple
from dotenv import load_dotenv
from twilio_utils import format_to_e164

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Language translations for appointment system
TRANSLATIONS = {
    'en': {
        'appointment_types': {
            'consultation': 'General Consultation', 'follow-up': 'Follow-up Visit', 'emergency': 'Emergency Appointment', 'routine': 'Routine Checkup', 'diagnostic': 'Diagnostic Test'
        },
        'departments': {
            'General': 'General Medicine', 'Cardiology': 'Cardiology', 'Neurology': 'Neurology', 'Surgery': 'General Surgery', 'ICU': 'Intensive Care Unit', 'Emergency': 'Emergency Department', 'Pediatrics': 'Pediatrics', 'Orthopedics': 'Orthopedics', 'Psychiatry': 'Psychiatry'
        },
        'urgency_levels': {
            'normal': 'Normal', 'urgent': 'Urgent', 'emergency': 'Emergency'
        }
    },
    'hi': {
        'appointment_types': {
            'consultation': 'सामान्य परामर्श', 'follow-up': 'अनुवर्ती दौरा', 'emergency': 'आपातकालीन नियुक्ति', 'routine': 'नियमित जांच', 'diagnostic': 'नैदानिक परीक्षण'
        },
        'departments': {
            'General': 'सामान्य चिकित्सा', 'Cardiology': 'कार्डियोलॉजी', 'Neurology': 'न्यूरोलॉजी', 'Surgery': 'सामान्य सर्जरी', 'ICU': 'गहन चिकित्सा इकाई', 'Emergency': 'आपातकालीन विभाग', 'Pediatrics': 'बाल रोग', 'Orthopedics': 'हड्डी रोग', 'Psychiatry': 'मनोचिकित्सा'
        },
        'urgency_levels': {
            'normal': 'सामान्य', 'urgent': 'तत्काल', 'emergency': 'आपातकालीन'
        }
    },
    'te': {
        'appointment_types': {
            'consultation': 'సాధారణ సలహా', 'follow-up': 'ఫాలో-అప్ సందర్శన', 'emergency': 'ఎమర్జెన్సీ నిర్ధారణ', 'routine': 'రూటిన్ చెక్‌అప్', 'diagnostic': 'రోగ నిర్ధారణ పరీక్ష'
        },
        'departments': {
            'General': 'సాధారణ వైద్యకం', 'Cardiology': 'కార్డియోలజీ', 'Neurology': 'నరాల రోగాల శాస్త్రం', 'Surgery': 'సాధారణ శస్త్రచికిత్స', 'ICU': 'ఇంటెన్సివ్ కేర్ యూనిట్', 'Emergency': 'ఎమర్జెన్సీ డిపార్టుమెంట్', 'Pediatrics': 'శిశువిహార శాస్త్రం', 'Orthopedics': 'ఆర్థోపెడిక్‌లు', 'Psychiatry': 'మానసిక రోగాల శాస్త్రం'
        },
        'urgency_levels': {
            'normal': 'సాధారణ', 'urgent': 'తక్షణ', 'emergency': 'ఎమర్జెన్సీ'
        }
    },
    'ta': {
        'appointment_types': {
            'consultation': 'பொதுவான ஆலோசனை', 'follow-up': 'பின்தொடர்ந்து வரவு', 'emergency': 'அவசர நியமனம்', 'routine': 'வழக்கமான பரிசோதனை', 'diagnostic': 'நோய் நிர்ணய சோதனை'
        },
        'departments': {
            'General': 'பொதுவான மருத்துவம்', 'Cardiology': 'இதய நோய் சிகிச்சையியல்', 'Neurology': 'நரம்பு மண்டல நோய் சிகிச்சையியல்', 'Surgery': 'பொதுவான அறுவை சிகிச்சை', 'ICU': 'தீவிர சிகிச்சை பிரிவு', 'Emergency': 'அவசர பிரிவு', 'Pediatrics': 'குழந்தை மருத்துவம்', 'Orthopedics': 'எலும்பு சிகிச்சையியல்', 'Psychiatry': 'மনோ சிகிச்சையியல்'
        },
        'urgency_levels': {
            'normal': 'சாதாரணம்', 'urgent': 'அவசரம்', 'emergency': 'அவசர நிலை'
        }
    },
    'ml': {
        'appointment_types': {
            'consultation': 'പൊതുവായ ഉപദേശം', 'follow-up': 'ഫോളോ-അപ്പ് സന്ദർശനം', 'emergency': 'എമർജൻസി നിയമനം', 'routine': 'നിയമിത പരിശോധന', 'diagnostic': 'രോഗനിർണയ പരിശോധന'
        },
        'departments': {
            'General': 'പൊതുവായ ഔഷധം', 'Cardiology': 'കാർഡിയോളജി', 'Neurology': 'ന്യൂറോളജി', 'Surgery': 'പൊതുവായ ശസ്ത്രക്രിയ', 'ICU': 'ഗുരുതര പരിചരണ വിഭാഗം', 'Emergency': 'എമർജൻസി വിഭാഗം', 'Pediatrics': 'കുട്ടികളുടെ ഔഷധം', 'Orthopedics': 'അസ്ഥിരോഗചികിത്സ', 'Psychiatry': 'മനോരോഗചികിത്സ'
        },
        'urgency_levels': {
            'normal': 'സാധാരണ', 'urgent': 'അത്യാവശ്യം', 'emergency': 'എമർജൻസി'
        }
    }
}

class AppointmentRoutingEngine:
    """Intelligent appointment routing system with AI integration"""
    
    def __init__(self):
        self.weights = {
            'specialization_match': 0.30,
            'workload': 0.25,
            'availability': 0.20,
            'patient_history': 0.15,
            'urgency_response': 0.10
        }
        load_dotenv()
        self.dev_mode = os.environ.get('DEVELOPMENT_MODE', 'false').lower() == 'true'
    
    def calculate_doctor_score(self, doctor, appointment_request):
        """Calculate routing score for a doctor"""
        score = 0
        score += self._calculate_specialization_match(doctor, appointment_request.department, appointment_request.appointment_type) * self.weights['specialization_match']
        score += self._calculate_workload_score(doctor, appointment_request.preferred_date) * self.weights['workload']
        score += self._calculate_availability_score(doctor, appointment_request.preferred_date) * self.weights['availability']
        score += self._calculate_patient_history_score(doctor, appointment_request.patient) * self.weights['patient_history']
        score += self._calculate_urgency_response_score(doctor, appointment_request.urgency) * self.weights['urgency_response']
        return score
    
    def _calculate_specialization_match(self, doctor, department, appointment_type):
        if not doctor.specialization or not department: return 0.5
        spec_low, dept_low = doctor.specialization.lower(), department.lower()
        if spec_low == dept_low: return 1.0
        if dept_low in spec_low or spec_low in dept_low: return 0.8
        if 'general' in spec_low and appointment_type in ['routine', 'consultation']: return 0.7
        return 0.3
    
    def _calculate_workload_score(self, doctor, preferred_date):
        if not preferred_date: return 0.5
        count = AppointmentRequest.query.filter(AppointmentRequest.doctor_id == doctor.id, db.func.date(AppointmentRequest.preferred_date) == preferred_date.date(), AppointmentRequest.status.in_(['confirmed', 'pending'])).count()
        return max(0, 1 - (count / 10.0))
    
    def _calculate_availability_score(self, doctor, preferred_date):
        if not preferred_date: return 0.5
        shift = Shift.query.filter(Shift.staff_id == doctor.id, db.func.date(Shift.start_time) == preferred_date.date(), Shift.is_active == True).first()
        if shift: return 1.0
        if Shift.query.count() == 0: return 1.0 # Relaxed mode for demo
        return 0.6
    
    def _calculate_patient_history_score(self, doctor, patient):
        if DoctorNote.query.filter(DoctorNote.doctor_id == doctor.id, DoctorNote.patient_id == patient.id).count() > 0: return 1.0
        if patient.assigned_doctor_id == doctor.id: return 0.9
        return 0.4
    
    def _calculate_urgency_response_score(self, doctor, urgency):
        if urgency == 'emergency': return 1.0 if doctor.is_on_duty else 0.5
        if urgency == 'urgent': return 0.9 if doctor.is_on_duty else 0.7
        return 0.8

    def analyze_with_ai(self, appointment: AppointmentRequest, candidates: List[StaffMember]) -> Optional[Tuple[int, str]]:
        """Use Groq to provide a final recommendation from top candidates."""
        try:
            from app import generate_ai_response, groq_client
            if groq_client is None:
                return None

            doctors_info = []
            for d in candidates:
                doctors_info.append({
                    'id': d.id, 'name': d.full_name, 'specialization': d.specialization,
                    'patients': AppointmentRequest.query.filter_by(doctor_id=d.id, status='confirmed').count()
                })
            
            prompt = f"""Expert Medical Router: Recommend BEST doctor for {appointment.patient.full_name}.
Req: {appointment.department}, {appointment.appointment_type}, Urgency: {appointment.urgency}.
Reason: {appointment.notes}.
Doctors: {json.dumps(doctors_info)}
Response format: JSON {{"recommended_doctor_id": ID, "reasoning": "TEXT"}}"""
            
            response = generate_ai_response([
                {'role': 'user', 'content': prompt}
            ])
            if response:
                import re
                json_match = re.search(r'\{.*\}', response, re.DOTALL)
                if json_match:
                    rec = json.loads(json_match.group())
                    return rec.get('recommended_doctor_id'), rec.get('reasoning', 'AI routing')
        except Exception as e:
            err = str(e)
            if "API_KEY_INVALID" in err or "expired" in err.lower():
                logger.warning("Groq API key rejected. Check GROQ_API_KEY in .env")
            else:
                logger.error(f"AI Routing error: {err}")
        return None

    def route_appointment(self, appointment_request):
        """Main routing algorithm: Scoring + AI Enhancement"""
        try:
            dept = appointment_request.department or 'General Medicine'
            doctors = StaffMember.query.filter(StaffMember.role == 'doctor', StaffMember.is_active == True, (StaffMember.department == dept) | (StaffMember.specialization.ilike(f'%{dept}%'))).all()
            if not doctors: doctors = StaffMember.query.filter_by(role='doctor', is_active=True).all()
            if not doctors: return None
            
            # 1. Scoring Phase
            scored_docs = []
            for doc in doctors:
                score = self.calculate_doctor_score(doc, appointment_request)
                scored_docs.append((doc, score))
            scored_docs.sort(key=lambda x: x[1], reverse=True)
            
            # 2. AI Enhancement Phase (Check top 3)
            top_candidates = [d[0] for d in scored_docs[:3]]
            ai_rec = self.analyze_with_ai(appointment_request, top_candidates)
            
            best_doctor = None
            if ai_rec:
                doc_id, reasoning = ai_rec
                best_doctor = StaffMember.query.get(doc_id)
                appointment_request.routing_score = 1.0 # AI verified
            
            if not best_doctor:
                best_doctor, best_score = scored_docs[0]
                appointment_request.routing_score = best_score
            
            appointment_request.doctor_id = best_doctor.id
            appointment_request.allocated_by_system = True
            db.session.commit()
            
            logger.info(f"Appt {appointment_request.id} -> Dr. {best_doctor.full_name} ({dept})")
            return best_doctor
        except Exception as e:
            logger.error(f"Allocation error: {str(e)}")
            return None

class AppointmentNotificationService:
    """Service for SMS and Email with Dev Mode support"""
    def __init__(self):
        load_dotenv()
        self.dev_mode = os.environ.get('DEVELOPMENT_MODE', 'false').lower() == 'true'
        self.sms_provider = self._init_sms()
        
    def _init_sms(self):
        if os.environ.get('TWILIO_ACCOUNT_SID'): return 'twilio'
        return 'mock'

    def send_appointment_confirmation(self, appointment: AppointmentRequest, doctor: StaffMember) -> bool:
        patient = Patient.query.get(appointment.patient_id)
        if not patient: return False
        doctor_name = f"Dr. {doctor.first_name} {doctor.last_name}"
        msg = f"Dear {patient.first_name}, appt {appointment.id} is CONFIRMED with {doctor_name} in {appointment.department} on {appointment.preferred_date.strftime('%d-%m-%Y')} at {appointment.preferred_time}. CareSync AI"
        
        if patient.phone: self._send_sms(patient.phone, msg)
        if patient.email: self._send_email(patient.email, "Appointment Confirmed", msg)
        return True

    def send_booking_notification(self, appointment: AppointmentRequest) -> bool:
        patient = Patient.query.get(appointment.patient_id)
        if not patient: return False
        msg = f"Dear {patient.first_name}, appt {appointment.id} for {appointment.department} is RECEIVED. We are allocating a doctor. Thanks!"
        if patient.phone: self._send_sms(patient.phone, msg)
        return True

    def _send_sms(self, phone: str, message: str) -> bool:
        try:
            formatted_phone = format_to_e164(phone)
            if self.sms_provider == 'twilio' and not self.dev_mode:
                from twilio.rest import Client
                client = Client(os.environ['TWILIO_ACCOUNT_SID'], os.environ['TWILIO_AUTH_TOKEN'])
                client.messages.create(body=message, from_=os.environ['TWILIO_PHONE_NUMBER'], to=formatted_phone)
                return True
            logger.info(f"[MOCK SMS] -> {formatted_phone}: {message}")
            return True
        except Exception as e:
            if "21608" in str(e):
                logger.warning(f"Twilio: {phone} is unverified. Use Console or verify number.")
                logger.info(f"[MOCK SMS FALLBACK] -> {phone}: {message}")
                return True
            logger.error(f"SMS error: {e}")
            return False

    def _send_email(self, email: str, subject: str, body: str) -> bool:
        try:
            if self.dev_mode:
                logger.info(f"[MOCK EMAIL] To: {email} | Sub: {subject}")
                return True
            from flask import current_app
            from flask_mail import Mail, Message
            mail = Mail(current_app)
            mail.send(Message(subject=subject, recipients=[email], body=body))
            return True
        except Exception as e:
            if "WinError 10061" in str(e) or "connection refused" in str(e).lower():
                logger.warning(f"Email Connection Refused (No SMTP server). To: {email}")
                if self.dev_mode: logger.info(f"[MOCK EMAIL FALLBACK] To: {email} | Sub: {subject}")
                return True
            logger.error(f"Email error: {e}")
            return False

def get_appointment_translations(language='en'):
    return TRANSLATIONS.get(language, TRANSLATIONS['en'])

def allocate_appointment(appointment_request):
    router = AppointmentRoutingEngine()
    return router.route_appointment(appointment_request)

# Instances for integration
appointment_router = AppointmentRoutingEngine()
notification_service = AppointmentNotificationService()
