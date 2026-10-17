"""CareSync AI - HTTP layer.

Every web route lives here.  Business logic is delegated to ``routing``
(patient assignment, alerts, re-routing) and ``services`` (messaging, vitals,
appointments, risk analytics) so this module stays a thin, readable
request/response layer.
"""

import json
import logging
import os
from datetime import datetime, timedelta
from functools import wraps

from flask import (Response, flash, jsonify, redirect, render_template,
                   request, session, url_for)
from flask_login import current_user

from app import app, db, generate_ai_response, socketio
from models import (Alert, AlertRerouting, AppointmentRequest, AuditLog,
                    ChatMessage, DoctorNote, LabReport, Medication,
                    MedicationAdministration, MedicineDetail, Notification,
                    Patient, Round, RiskAssessment, Shift, ShiftHandoff,
                    StaffMember, TreatmentLog, VitalSign, Ward)
from routing import (acknowledge_alert, alert_recipients, dashboards_open,
                     mark_dashboard_open, monitoring_status, open_dashboards,
                     patient_has_doctor, patient_has_nurse, reroute_patient,
                     staff_can_see_alert, workload_report)
from services import (DOSAGE_INTERVAL_HOURS, appointment_router,
                      create_predictive_alert, format_to_e164,
                      get_appointment_translations, messaging,
                      notification_service, risk_predictor, send_to_n8n_webhook,
                      send_vitals_alert_sms, translate_text)

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


def generate_fallback_response(patient, user_message, language='en'):
    """Generate intelligent fallback responses based on patient data in multiple languages"""
    msg_lower = user_message.lower()
    
    # Get patient data
    active_meds = Medication.query.filter_by(patient_id=patient.id, is_active=True).all()
    latest_vital = patient.vitals.order_by(VitalSign.recorded_at.desc()).first()
    appointments = AppointmentRequest.query.filter_by(patient_id=patient.id, status='confirmed').count()
    
    # ===== HINDI RESPONSES =====
    if language == 'hi':
        if any(word in msg_lower for word in ['medicine', 'medication', 'दवा', 'दवाएं', 'ilaaj']):
            if active_meds:
                meds_text = ', '.join([f"{m.name} ({m.dosage}, {m.frequency})" for m in active_meds])
                return f"आपकी वर्तमान दवाएं:\n{meds_text}\n\nकृपया इन दवाओं के बारे में अधिक जानकारी के लिए अपने डॉक्टर से परामर्श करें।"
            return "आपके पास वर्तमान में कोई सक्रिय दवा नहीं है।"
        
        elif any(word in msg_lower for word in ['blood', 'bp', 'ब्लड प्रेशर', 'रक्तचाप', 'vital']):
            if latest_vital:
                return f"आपके नवीनतम महत्वपूर्ण संकेत:\n• रक्तचाप: {latest_vital.blood_pressure_systolic}/{latest_vital.blood_pressure_diastolic} mmHg\n• हृदय गति: {latest_vital.heart_rate} bpm\n• ऑक्सीजन स्तर: {latest_vital.oxygen_saturation}%"
            return "आपके महत्वपूर्ण संकेत डेटा उपलब्ध नहीं हैं।"
        
        elif any(word in msg_lower for word in ['diagnosis', 'बीमारी', 'रोग', 'निदान', 'condition']):
            return f"आपका निदान: {patient.diagnosis}\n\nअधिक विस्तृत जानकारी और उपचार योजना के लिए कृपया अपने डॉक्टर से बात करें।"
        
        elif any(word in msg_lower for word in ['appointment', 'schedule', 'नियुक्ति', 'मिलना']):
            return f"आपके पास {appointments} पुष्टि किए गए नियुक्तियां हैं।\n\nनई नियुक्ति बुक करने के लिए 'नियुक्तियां' टैब का उपयोग करें।"
        
        elif any(word in msg_lower for word in ['emergency', 'urgent', 'मदद', 'दर्द', 'जरूरी']):
            return "⚠️ यदि आप किसी आपातकाल में हैं, तो कृपया तुरंत अपनी नजदीकी आपातकालीन सेवा को कॉल करें या अस्पताल जाएं।"
        
        elif any(word in msg_lower for word in ['diet', 'exercise', 'lifestyle', 'खान', 'व्यायाम', 'जीवन']):
            return "स्वस्थ जीवनशैली के लिए सुझाव:\n• नियमित व्यायाम करें (दिन में 30 मिनट)\n• संतुलित आहार लें\n• पर्याप्त पानी पिएं\n• पर्याप्त नींद लें\n\nअपने व्यक्तिगत स्वास्थ्य योजना के लिए अपने डॉक्टर से परामर्श करें।"
        
        return "नमस्ते! मैं CareSync AI सहायक हूँ। मैं आपकी दवाओं, महत्वपूर्ण संकेतों और स्वास्थ्य रिकॉर्ड के बारे में मदद कर सकता हूँ। चिकित्सा सलाह के लिए कृपया अपने डॉक्टर से संपर्क करें।"
    
    # ===== TAMIL RESPONSES =====
    elif language == 'ta':
        if any(word in msg_lower for word in ['medicine', 'medication', 'மருந்து', 'மாத்திரை']):
            if active_meds:
                meds_text = ', '.join([f"{m.name} ({m.dosage}, {m.frequency})" for m in active_meds])
                return f"உங்கள் தற்போதைய மருந்துகள்:\n{meds_text}\n\nகூடுதல் விவரங்களுக்கு உங்கள் மருத்துவரை அணுகவும்."
            return "உங்களுக்கு தற்போது சக்रிய மருந்துகள் இல்லை."
        
        elif any(word in msg_lower for word in ['blood', 'bp', 'vital', 'இரத்த']):
            if latest_vital:
                return f"உங்கள் சமீபத்திய உயிரணு அறிகுறிகள்:\n• இரத்த அழுத்தம்: {latest_vital.blood_pressure_systolic}/{latest_vital.blood_pressure_diastolic} mmHg\n• நாடித்துடிப்பு: {latest_vital.heart_rate} bpm\n• ஆக்சிஜன் அளவு: {latest_vital.oxygen_saturation}%"
            return "உயிரணு தரவு கிடைக்கவில்லை."
        
        elif any(word in msg_lower for word in ['diagnosis', 'நோய்', 'கோளாறு']):
            return f"உங்கள் நோயறிதல்: {patient.diagnosis}\n\nமேலும் சிகிச்சை பற்றிய தகவலுக்கு உங்கள் மருத்துவரைப் பாருங்கள்."
        
        elif any(word in msg_lower for word in ['appointment', 'ஆலோசனை', 'சந்திப்பு']):
            return f"உங்களுக்கு {appointments} நிশ்চயம் செய்யப்பட்ட ஆலோசனை உள்ளது।\n\nநতுன ஆலோசனை பதிவு செய்ய 'ஆலோசனைகள்' தாவணியைப் பயன்படுத்தவும்."
        
        elif any(word in msg_lower for word in ['emergency', 'urgent', 'கட்டாய', 'உதவி', 'அவசரம்']):
            return "⚠️ அவசரநிலையில் இருந்தால், உடனடியாக அருகிலுள்ள அவசர சேவையை அழைக்கவும்."
        
        elif any(word in msg_lower for word in ['diet', 'exercise', 'lifestyle', 'உணவு', 'உடற்பயிற்சி', 'வாழ்க்கை']):
            return "ஆரோக்கியமான வாழ்க்கை முறை குறிப்புகள்:\n• தினமும் உடற்பயிற்சி செய்யுங்கள் (30 நிமிடங்கள்)\n• சத்தான உணவை உண்ணுங்கள்\n• போதுமான தண்ணீர் குடிங்க\n• போதுமான அளவு தூங்குங்கள்\n\nதனிப்பட்ட ஆலோசனைக்கு மருத்துவரை அணுகவும்."
        
        return "வணக்கம்! நான் CareSync AI உதவியாளி. மருந்து, உயிரணு மற்றும் உடல்நலம் பற்றி உங்களுக்கு உதவ முடியும். மருத்துவ ஆலோசனைக்கு உங்கள் மருத்துவரை அணுகவும்."
    
    # ===== TELUGU RESPONSES =====
    elif language == 'te':
        if any(word in msg_lower for word in ['medicine', 'medication', 'మందు', 'ఔషధం']):
            if active_meds:
                meds_text = ', '.join([f"{m.name} ({m.dosage}, {m.frequency})" for m in active_meds])
                return f"మీ ప్రస్తుత మందులు:\n{meds_text}\n\nమరిన్ని వివరాల కోసం దయచేసి మీ డాక్టర్‌ను సంప్రదించండి."
            return "మీకు ప్రస్తుతం సక్రియ మందులు లేవు."
        
        elif any(word in msg_lower for word in ['blood', 'bp', 'vital', 'రక్తం']):
            if latest_vital:
                return f"మీ సమీప జీవన సంకేతాలు:\n• రక్త పీడనం: {latest_vital.blood_pressure_systolic}/{latest_vital.blood_pressure_diastolic} mmHg\n• హృదయ స్పందన: {latest_vital.heart_rate} bpm\n• ఆక్సిజన్ స్థాయి: {latest_vital.oxygen_saturation}%"
            return "జీవన సంకేత డేటా అందుబాటులో లేదు."
        
        elif any(word in msg_lower for word in ['diagnosis', 'వ్యాధి', 'రোగం']):
            return f"మీ రోగ నిర్ధారణ: {patient.diagnosis}\n\nలేని వివరాల కోసం దయచేసి మీ డాక్టర్‌ను సంప్రదించండి."
        
        elif any(word in msg_lower for word in ['appointment', 'ప్రస్తారణ']):
            return f"మీకు {appointments} నిశ్చితమైన అపాయింట్‌మెంట్‌లు ఉన్నాయి।\n\nవాటిని రిజిస్టర్ చేయడానికి 'అపాయింట్‌మెంట్‌లు' ట్యాబ్‌ని ఉపయోగించండి."
        
        elif any(word in msg_lower for word in ['emergency', 'urgent', 'తక్షణ', 'సహాయం', 'అత్యవసర']):
            return "⚠️ ఎమర్జెన్సీ సందర్భంలో దయచేసి తక్షణమే అతిత్వర సేవలను అందించండి."
        
        elif any(word in msg_lower for word in ['diet', 'exercise', 'lifestyle', 'ఆహారం', 'వ్యాయామం', 'జీవనశైలి']):
            return "ఆరోగ్యకరమైన జీవనశైలి చిట్కాలు:\n• క్రమం తప్పకుండా వ్యాయామం చేయండి (రోజుకు 30 నిమిషాలు)\n• సమతుల్య ఆహారం తీసుకోండి\n• తగినంత నీరు త్రాగాలి\n• తగినంత నిద్రపోవాలి\n\nవ్యక్తిగత సలహా కోసం డాక్టర్‌ని సంప్రదించండి."
        
        return "సలాం! నేను CareSync AI సహాయకుడిని. మందులు, జీవన సంకేతాలు మరియు ఆరోగ్య రికార్డల గురించి మీకు సహాయం చేయగలను. వైద్యపరమైన సలహా కోసం దయచేసి మీ డాక్టర్‌ను సంప్రదించండి."
    
    # ===== MALAYALAM RESPONSES =====
    elif language == 'ml':
        if any(word in msg_lower for word in ['medicine', 'medication', 'മരുന്ന്', 'ഔഷധം']):
            if active_meds:
                meds_text = ', '.join([f"{m.name} ({m.dosage}, {m.frequency})" for m in active_meds])
                return f"നിങ്ങളുടെ നിലവിലെ മരുന്നുകൾ:\n{meds_text}\n\nകൂടുതൽ വിവരങ്ങൾക്കായി ദയവായി നിങ്ങളുടെ ഡോക്ടറെ സന്ദർശിക്കുക."
            return "നിങ്ങൾക്കിപ്പോൾ സജീവ മരുന്നുകൾ ഇല്ല."
        
        elif any(word in msg_lower for word in ['blood', 'bp', 'vital', 'രക്തം']):
            if latest_vital:
                return f"നിങ്ങളുടെ സമീപകാല ജീവിത സൂചകങ്ങൾ:\n• രക്ത സമ്മർദ്ദം: {latest_vital.blood_pressure_systolic}/{latest_vital.blood_pressure_diastolic} mmHg\n• ഹൃദയ ബീറ്റ്: {latest_vital.heart_rate} bpm\n• ഓക്സിജൻ നിലവാരം: {latest_vital.oxygen_saturation}%"
            return "ജീവിത സൂചക ഡാറ്റ ലഭ്യമല്ല."
        
        elif any(word in msg_lower for word in ['diagnosis', 'രോഗം', 'വ്യാധി']):
            return f"നിങ്ങളുടെ രോഗ നിർണ്ണയം: {patient.diagnosis}\n\nകൂടുതൽ വിവരങ്ങൾക്കായി നിങ്ങളുടെ ഡോക്ടറെ സന്ദർശിക്കുക."
        
        elif any(word in msg_lower for word in ['appointment', 'നിയമനം']):
            return f"നിങ്ങൾക്ക് {appointments} സ്ഥിരീകൃത നിയമനങ്ങൾ ഉണ്ട്।\n\nപുതിയ നിയമനം ബുക്ക് ചെയ്യാൻ 'നിയമനങ്ങൾ' ടാബ് ഉപയോഗിക്കുക."
        
        elif any(word in msg_lower for word in ['emergency', 'urgent', 'അടിയന്തരം', 'സഹായം']):
            return "⚠️ അടിയന്തര സാഹചര്യത്തിൽ, ദയവായി ഉടൻ നിങ്ങളുടെ അടിയന്തര സേവനങ്ങളോട് ബന്ധപ്പെടുക."
        
        elif any(word in msg_lower for word in ['diet', 'exercise', 'lifestyle', 'ഭക്ഷണം', 'വ്യായാമം', 'ജീവിതശൈലി']):
            return "ആరోగ్యకరమైన ജീവിതശൈലി നിർദ്ദേശങ്ങൾ:\n• പതിവായി വ്യായാമം ചെയ്യുക (ദിവസവും 30 മിനിറ്റ്)\n• സമീകൃതാഹാരം കഴിക്കുക\n• ആവശ്യത്തിന് വെള്ളം കുടിക്കുക\n• ആവശ്യത്തിന് ഉറങ്ങുക\n\nകൂടുതൽ വിവരങ്ങൾക്കായി ഡോക്ടറെ സമീപിക്കുക."
        
        return "സ്വാഗതം! ഞാൻ CareSync AI സഹായകൻ. മരുന്നുകൾ, ജീവിത സൂചകങ്ങൾ, ആരോഗ്യ രേഖകൾ എന്നിവ പറ്റി നിങ്ങളെ സഹായിക്കാൻ കഴിയും. വൈദ്യ സലാഹത്തിന് ദയവായി നിങ്ങളുടെ ഡോക്ടറെ സന്ദർശിക്കുക."
    
    # ===== ENGLISH FALLBACK =====
    else:
        if any(word in msg_lower for word in ['medicine', 'medication', 'drug', 'pills', 'pharma']):
            if active_meds:
                meds_text = ', '.join([f"{m.name} ({m.dosage}, {m.frequency})" for m in active_meds])
                return f"Your current medications:\n{meds_text}\n\nPlease consult your doctor for more details."
            return "You currently have no active medications on record."
        
        elif any(word in msg_lower for word in ['blood', 'bp', 'vitals', 'vital signs', 'pressure']):
            if latest_vital:
                return f"Your latest vital signs:\n• Blood Pressure: {latest_vital.blood_pressure_systolic}/{latest_vital.blood_pressure_diastolic} mmHg\n• Heart Rate: {latest_vital.heart_rate} bpm\n• Oxygen Level: {latest_vital.oxygen_saturation}%"
            return "Vital signs data not available."
        
        elif any(word in msg_lower for word in ['diagnosis', 'condition', 'disease', 'diagnosed']):
            return f"Your diagnosis: {patient.diagnosis}\n\nFor detailed information about your condition and treatment plan, please consult your doctor."
        
        elif any(word in msg_lower for word in ['appointment', 'schedule', 'book', 'visit', 'meeting']):
            return f"You have {appointments} confirmed appointments.\n\nTo book a new appointment, use the Appointments tab in the navigation menu."
        
        elif any(word in msg_lower for word in ['emergency', 'urgent', 'help', 'pain', 'critical']):
            return "⚠️ If you're experiencing an emergency, please call 911 or visit your nearest emergency room immediately."
        
        elif any(word in msg_lower for word in ['diet', 'exercise', 'lifestyle', 'health', 'fitness']):
            return "Health Lifestyle Tips:\n• Exercise regularly (30 minutes daily)\n• Eat a balanced diet\n• Stay hydrated\n• Get adequate sleep\n• Manage stress\n\nConsult your doctor for personalized advice."
        
        return "Hello! I'm CareSync AI Assistant. I can help with information about your medications, vital signs, and health records. For medical advice, please contact your healthcare provider."


@app.before_request
def make_session_permanent():
    session.permanent = True


def get_staff_user():
    if 'staff_id' in session:
        return StaffMember.query.filter_by(id=session['staff_id']).first()
    return None


def staff_login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        staff = get_staff_user()
        if not staff:
            flash('Please log in to access this page.', 'warning')
            return redirect(url_for('staff_login'))
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        staff = get_staff_user()
        if not staff or staff.role != 'admin':
            flash('Admin access required.', 'danger')
            return redirect(url_for('staff_login'))
        return f(*args, **kwargs)
    return decorated_function


def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            staff = get_staff_user()
            if not staff or staff.role not in roles:
                flash('You do not have permission to access this page.', 'danger')
                return redirect(url_for('dashboard'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator


@app.route('/health')
def health_check():
    """Simple health check endpoint"""
    return jsonify({'status': 'ok', 'message': 'Server is running'}), 200


@app.route('/')
def index():
    staff = get_staff_user()
    if staff:
        return redirect(url_for('dashboard'))
    return render_template('index.html', now=datetime.now())


@app.route('/login', methods=['GET', 'POST'])
def staff_login():
    if get_staff_user():
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        identifier = request.form.get('staff_id', '').strip()
        password = request.form.get('password', '')

        # Allow login using either staff ID (e.g. DOC0001) or email address
        staff = None
        try:
            if '@' in identifier:
                staff = StaffMember.query.filter_by(email=identifier.lower(), is_active=True).first()
            else:
                staff = StaffMember.query.filter_by(staff_id=identifier.upper(), is_active=True).first()
        except Exception:
            staff = None

        if staff and staff.check_password(password):
            session['staff_id'] = staff.id
            session['staff_role'] = staff.role
            
            # Audit Login
            try:
                log = AuditLog(
                    action='LOGIN',
                    table_name='auth',
                    record_id=staff.id,
                    field_name='session',
                    old_value=None,
                    new_value='active',
                    changed_by_id=staff.id
                )
                db.session.add(log)
                db.session.commit()
            except Exception as e:
                logging.error(f"Failed to log login audit: {e}")
                
            flash(f'Welcome back, {staff.full_name}!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid Staff ID / email or password.', 'danger')
            # Optional: Log failed attempts if needed (careful with spam)
    
    return render_template('index.html', now=datetime.now())

@app.route('/logout')
def logout():
    staff = get_staff_user()
    if staff:
        try:
            log = AuditLog(
                action='LOGOUT',
                table_name='auth',
                record_id=staff.id,
                field_name='session',
                old_value='active',
                new_value='terminated',
                changed_by_id=staff.id
            )
            db.session.add(log)
            db.session.commit()
        except:
            pass
            
    session.pop('staff_id', None)
    session.pop('staff_role', None)
    flash('You have been logged out.', 'info')
    return redirect(url_for('staff_login'))





@app.route('/dashboard')
@staff_login_required
def dashboard():
    staff = get_staff_user()
    
    if staff.role == 'admin':
        return redirect(url_for('admin_dashboard'))
    elif staff.role == 'doctor':
        return redirect(url_for('doctor_dashboard'))
    elif staff.role == 'nurse':
        return redirect(url_for('nurse_dashboard'))
    
    return redirect(url_for('index'))


@app.route('/ward/<department_name>')
@staff_login_required
def ward_dashboard(department_name):
    """Specific dashboard for a department/ward"""
    staff = get_staff_user()
    
    # Fetch patients in this department, exclude discharged
    patients = Patient.query.filter(
        Patient.department == department_name, 
        Patient.status != 'discharged'
    ).order_by(Patient.room_number).all()
    
    # Quick stats
    critical_count = 0
    stable_count = 0
    for p in patients:
        lv = p.latest_vitals
        if lv and lv.status == 'critical':
            critical_count += 1
        elif lv and lv.status == 'normal':
            stable_count += 1
            
    # Staff on duty in this department
    staff_on_duty_count = StaffMember.query.filter_by(
        department=department_name, 
        is_on_duty=True
    ).count()
    
    return render_template('ward_dashboard.html', 
        staff=staff,
        department=department_name,
        patients=patients,
        critical_count=critical_count,
        stable_count=stable_count,
        staff_on_duty_count=staff_on_duty_count
    )


@app.route('/admin')
@staff_login_required
@admin_required
def admin_dashboard():
    staff = get_staff_user()
    
    _TEST_STAFF_IDS = ['DOC_RT', 'NRS_RT', 'NRS_ICU', 'NRS_ER']
    total_patients = Patient.query.filter(Patient.status != 'discharged').count()
    total_doctors = StaffMember.query.filter(
        StaffMember.role == 'doctor', StaffMember.is_active == True,
        ~StaffMember.staff_id.in_(_TEST_STAFF_IDS)
    ).count()
    total_nurses = StaffMember.query.filter(
        StaffMember.role == 'nurse', StaffMember.is_active == True,
        ~StaffMember.staff_id.in_(_TEST_STAFF_IDS)
    ).count()
    on_duty_doctors = StaffMember.query.filter(
        StaffMember.role == 'doctor', StaffMember.is_on_duty == True,
        StaffMember.is_active == True, ~StaffMember.staff_id.in_(_TEST_STAFF_IDS)
    ).count()
    on_duty_nurses = StaffMember.query.filter(
        StaffMember.role == 'nurse', StaffMember.is_on_duty == True,
        StaffMember.is_active == True, ~StaffMember.staff_id.in_(_TEST_STAFF_IDS)
    ).count()
    critical_alerts = Alert.query.filter_by(is_acknowledged=False, severity='critical').count()
    
    icu_patients = Patient.query.filter_by(status='icu').count()
    emergency_patients = Patient.query.filter_by(status='emergency').count()
    
    doctors = StaffMember.query.filter(
        StaffMember.role == 'doctor', StaffMember.is_active == True,
        ~StaffMember.staff_id.in_(_TEST_STAFF_IDS)
    ).all()
    nurses = StaffMember.query.filter(
        StaffMember.role == 'nurse', StaffMember.is_active == True,
        ~StaffMember.staff_id.in_(_TEST_STAFF_IDS)
    ).all()
    
    recent_alerts = Alert.query.filter_by(is_acknowledged=False).order_by(Alert.created_at.desc()).limit(10).all()
    rerouting_count = AlertRerouting.query.count()
    
    return render_template('admin/dashboard.html',
        staff=staff,
        total_patients=total_patients,
        total_doctors=total_doctors,
        total_nurses=total_nurses,
        on_duty_doctors=on_duty_doctors,
        on_duty_nurses=on_duty_nurses,
        critical_alerts=critical_alerts,
        icu_patients=icu_patients,
        emergency_patients=emergency_patients,
        doctors=doctors,
        nurses=nurses,
        recent_alerts=recent_alerts,
        rerouting_count=rerouting_count
    )


@app.route('/admin/rerouting-logs')
@staff_login_required
@admin_required
def admin_rerouting_logs():
    staff = get_staff_user()
    logs = AlertRerouting.query.order_by(AlertRerouting.timestamp.desc()).all()
    return render_template('admin/rerouting_logs.html', staff=staff, logs=logs)


@app.route('/admin/users')
@staff_login_required
@admin_required
def admin_users():
    staff = get_staff_user()
    _TEST_STAFF_IDS = ['DOC_RT', 'NRS_RT', 'NRS_ICU', 'NRS_ER']
    all_staff = StaffMember.query.filter(
        StaffMember.role != 'admin',
        ~StaffMember.staff_id.in_(_TEST_STAFF_IDS)
    ).order_by(StaffMember.role, StaffMember.last_name).all()
    return render_template('admin/users.html', staff=staff, all_staff=all_staff)


@app.route('/admin/register', methods=['GET', 'POST'])
@staff_login_required
@admin_required
def admin_register():
    staff = get_staff_user()
    
    if request.method == 'POST':
        role = request.form.get('role')
        first_name = request.form.get('first_name', '').strip()
        last_name = request.form.get('last_name', '').strip()
        email = request.form.get('email', '').strip()
        phone = request.form.get('phone', '').strip()
        department = request.form.get('department', '').strip()
        specialization = request.form.get('specialization', '').strip()
        password = request.form.get('password', '')
        
        if not all([role, first_name, last_name, email, password]):
            flash('Please fill in all required fields.', 'danger')
            return render_template('admin/register.html', staff=staff)

        # Create new staff member
        prefix = {'doctor': 'DOC', 'nurse': 'NRS'}
        count = StaffMember.query.filter_by(role=role).count() + 1
        staff_id = f"{prefix.get(role, 'STF')}{str(count).zfill(4)}"

        while StaffMember.query.filter_by(staff_id=staff_id).first():
            count += 1
            staff_id = f"{prefix.get(role, 'STF')}{str(count).zfill(4)}"

        new_staff = StaffMember(
            staff_id=staff_id,
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone=phone,
            role=role,
            department=department,
            specialization=specialization if role == 'doctor' else None,
            is_on_duty=False,
            is_active=True
        )
        new_staff.set_password(password)

        db.session.add(new_staff)
        db.session.commit()

        flash(f'{role.title()} registered successfully. Staff ID: {staff_id}', 'success')
        return redirect(url_for('admin_users'))
    return render_template('admin/register.html', staff=staff)
# ---------------------------------------------------------------------------
# Admin staff patient view
# ---------------------------------------------------------------------------
@app.route('/admin/staff/<int:staff_id>/patients')
@staff_login_required
@admin_required
def admin_staff_patients(staff_id):
    staff = StaffMember.query.get_or_404(staff_id)
    # Fetch patients where this staff is assigned as doctor or nurse
    patients = Patient.query.filter(
        (Patient.assigned_doctor_id == staff.id) |
        (Patient.assigned_nurse_id == staff.id)
    ).order_by(Patient.admission_date.desc()).all()
    return render_template('admin/staff_patients.html', staff=staff, patients=patients)



    
    return render_template('admin/register.html', staff=staff)


@app.route('/admin/staff/<int:staff_id>/toggle-duty', methods=['POST'])
@staff_login_required
@admin_required
def toggle_duty(staff_id):
    target_staff = StaffMember.query.get_or_404(staff_id)
    target_staff.is_on_duty = not target_staff.is_on_duty
    db.session.commit()
    return redirect(url_for('admin_users'))


@app.route('/admin/staff/<int:staff_id>/toggle-active', methods=['POST'])
@staff_login_required
@admin_required
def toggle_active(staff_id):
    target_staff = StaffMember.query.get_or_404(staff_id)
    target_staff.is_active = not target_staff.is_active
    db.session.commit()
    return redirect(url_for('admin_users'))


@app.route('/admin/patients')
@staff_login_required
@admin_required
def admin_patients():
    staff = get_staff_user()
    patients = Patient.query.order_by(Patient.admission_date.desc()).all()
    return render_template('admin/patients.html', staff=staff, patients=patients)


@app.route('/admin/appointments')
@staff_login_required
@admin_required
def admin_appointments():
    staff = get_staff_user()
    appointments = AppointmentRequest.query.order_by(AppointmentRequest.requested_at.desc()).all()
    return render_template('admin/appointments.html', staff=staff, appointments=appointments)


@app.route('/api/admin/appointment/<int:appt_id>/ai-routing-details', methods=['GET'])
@staff_login_required
@admin_required
def get_appointment_routing_details(appt_id):
    """Get AI routing details for an appointment (for admin dashboard)"""
    appt = AppointmentRequest.query.get_or_404(appt_id)
    
    # Get available doctors for comparison
    available_doctors = appointment_router.get_available_doctors(
        appt.department or 'General',
        appt.preferred_date,
        appt.appointment_type or 'consultation'
    )
    
    allocated_doctor = None
    if appt.doctor_id:
        allocated_doctor = StaffMember.query.get(appt.doctor_id)
    
    return jsonify({
        'appointment_id': appt.id,
        'patient': f"{appt.patient.first_name} {appt.patient.last_name}",
        'department': appt.department,
        'appointment_type': appt.appointment_type,
        'urgency': appt.urgency,
        'allocated_doctor': f"Dr. {allocated_doctor.first_name} {allocated_doctor.last_name}" if allocated_doctor else None,
        'allocated_by_system': appt.allocated_by_system,
        'routing_score': float(appt.routing_score) if appt.routing_score else None,
        'available_doctors_considered': len(available_doctors),
        'available_doctors': [
            {
                'id': d['id'],
                'name': d['name'],
                'specialization': d['specialization'],
                'workload': d['workload'],
                'available': d['available']
            } for d in available_doctors
        ]
    })


@app.route('/admin/appointment/<int:appt_id>/confirm', methods=['POST'])
@staff_login_required
def confirm_appointment(appt_id):
    # Depending on requirements, maybe only admin or doctors can confirm
    staff = get_staff_user()
    if staff.role not in ['admin', 'doctor']:
        flash('Permission denied.', 'danger')
        return redirect(url_for('dashboard'))
        
    appt = AppointmentRequest.query.get_or_404(appt_id)
    
    # If no doctor is assigned yet, use AI routing
    if not appt.doctor_id:
        allocated_doctor = appointment_router.route_appointment(appt)
        if allocated_doctor:
            logging.info(f"AI allocated appointment {appt_id} to Dr. {allocated_doctor.first_name} {allocated_doctor.last_name}")
        else:
            logging.warning(f"Could not allocate doctor for appointment {appt_id}")
    
    appt.status = 'confirmed'
    db.session.commit()
    
    # Send confirmation notification to patient
    notification_service.send_appointment_confirmation(appt, appt.doctor)
    
    flash('Appointment confirmed and notification sent to patient.', 'success')
    return redirect(request.referrer or url_for('admin_appointments'))



@app.route('/doctor')
@staff_login_required
@role_required('doctor', 'admin')
def doctor_dashboard():
    staff = get_staff_user()
    
    if staff.role == 'admin':
        patients = Patient.query.filter(Patient.status != 'discharged').all()
        active_alerts = []
        critical_alerts = []
    else:
        patients = Patient.query.filter_by(assigned_doctor_id=staff.id).filter(Patient.status != 'discharged').all()
        
        if staff.is_on_duty:
            patient_ids = [p.id for p in patients]
            active_alerts = Alert.query.filter(
                Alert.patient_id.in_(patient_ids),
                Alert.is_acknowledged == False
            ).order_by(Alert.severity.desc(), Alert.created_at.desc()).all()
            critical_alerts = [a for a in active_alerts if a.severity == 'critical']
        else:
            active_alerts = []
            critical_alerts = []
    
    return render_template('doctor/dashboard.html',
        staff=staff,
        patients=patients,
        active_alerts=active_alerts,
        critical_alerts=critical_alerts,
        todays_rounds=Round.query.filter(
            Round.doctor_id == staff.id, 
            Round.scheduled_time >= datetime.now().replace(hour=0, minute=0, second=0),
            Round.scheduled_time < datetime.now().replace(hour=0, minute=0, second=0) + __import__('datetime').timedelta(days=1)
        ).order_by(Round.scheduled_time).all()
    )


@app.route('/doctor/schedule-round', methods=['POST'])
@staff_login_required
@role_required('doctor')
def schedule_round():
    staff = get_staff_user()
    patient_id = request.form.get('patient_id')
    scheduled_time_str = request.form.get('scheduled_time')
    notes = request.form.get('notes')

    if not patient_id or not scheduled_time_str:
        flash('Missing required fields', 'danger')
        return redirect(url_for('doctor_dashboard'))

    try:
        # Combine today's date with the time
        time_obj = datetime.strptime(scheduled_time_str, '%H:%M').time()
        scheduled_datetime = datetime.combine(datetime.now().date(), time_obj)
        
        round_entry = Round(
            doctor_id=staff.id,
            patient_id=patient_id,
            scheduled_time=scheduled_datetime,
            notes=notes,
            status='pending'
        )
        db.session.add(round_entry)
        db.session.commit()
        flash('Round scheduled successfully', 'success')
    except Exception as e:
        flash(f'Error scheduling round: {e}', 'danger')

    return redirect(url_for('doctor_dashboard'))


@app.route('/doctor/round/<int:round_id>/complete', methods=['POST'])
@staff_login_required
@role_required('doctor')
def complete_round(round_id):
    round_entry = Round.query.get_or_404(round_id)
    if round_entry.doctor_id != session.get('staff_id'):
         flash('Unauthorized', 'danger')
         return redirect(url_for('doctor_dashboard'))
    
    round_entry.status = 'completed'
    db.session.commit()
    flash('Round marked as completed', 'success')
    return redirect(url_for('doctor_dashboard'))


@app.route('/doctor/note/<int:patient_id>/add', methods=['GET', 'POST'])
@staff_login_required
@role_required('doctor')
def add_doctor_note(patient_id):
    staff = get_staff_user()
    patient = Patient.query.get_or_404(patient_id)

    if request.method == 'POST':
        note_type = request.form.get('note_type')
        subjective = request.form.get('subjective')
        objective = request.form.get('objective')
        assessment = request.form.get('assessment')
        plan = request.form.get('plan')

        note = DoctorNote(
            patient_id=patient.id,
            doctor_id=staff.id,
            note_type=note_type,
            subjective=subjective,
            objective=objective,
            assessment=assessment,
            plan=plan
        )
        db.session.add(note)
        db.session.commit()
        flash('Note added successfully', 'success')
        return redirect(url_for('patient_detail', patient_id=patient.id))

    return render_template('doctor/add_note.html', staff=staff, patient=patient)



@app.route('/nurse')
@staff_login_required
@role_required('nurse', 'admin')
def nurse_dashboard():
    staff = get_staff_user()
    
    if staff.role == 'admin':
        patients = Patient.query.filter(Patient.status != 'discharged').all()
        active_alerts = []
    else:
        patients = Patient.query.filter_by(assigned_nurse_id=staff.id).filter(Patient.status != 'discharged').all()
        
        if staff.is_on_duty:
            patient_ids = [p.id for p in patients]
            active_alerts = Alert.query.filter(
                Alert.patient_id.in_(patient_ids),
                Alert.is_acknowledged == False
            ).order_by(Alert.severity.desc(), Alert.created_at.desc()).all()
        else:
            active_alerts = []
    
    medications_due = Medication.query.filter(
        Medication.is_active == True,
        Medication.next_due <= datetime.now()
    ).all()
    
    return render_template('nurse/dashboard.html',
        staff=staff,
        patients=patients,
        active_alerts=active_alerts,
        medications_due=medications_due
    )


@app.route('/patient/<int:patient_id>')
@staff_login_required
def patient_detail(patient_id):
    try:
        staff = get_staff_user()
        patient = Patient.query.get_or_404(patient_id)
        
        # Audit View (Full Record Access)
        try:
            log = AuditLog(
                action='VIEW',
                table_name='patients',
                record_id=patient.id,
                field_name='all',
                old_value=None,
                new_value='full_detail_access',
                changed_by_id=staff.id
            )
            db.session.add(log)
            db.session.commit()
        except Exception as e:
            logging.error(f"Audit Log Error: {e}")
        
        vitals = VitalSign.query.filter_by(patient_id=patient_id).order_by(VitalSign.recorded_at.desc()).limit(50).all()
        medications = Medication.query.filter_by(patient_id=patient_id, is_active=True).all()
        # load lab reports for the patient
        lab_reports = LabReport.query.filter_by(patient_id=patient_id).order_by(LabReport.reported_at.desc()).limit(20).all()
        alerts = Alert.query.filter_by(patient_id=patient_id).order_by(Alert.created_at.desc()).limit(20).all()
        treatment_logs = TreatmentLog.query.filter_by(patient_id=patient_id).order_by(TreatmentLog.performed_at.desc()).limit(20).all()
        
        # Serialize vitals for use in client-side charts (JSON-serializable)
        vitals_json = []
        for v in vitals:
            vitals_json.append({
                'id': v.id,
                'heart_rate': v.heart_rate,
                'blood_pressure_systolic': v.blood_pressure_systolic,
                'blood_pressure_diastolic': v.blood_pressure_diastolic,
                'oxygen_saturation': v.oxygen_saturation,
                'temperature': v.temperature,
                'respiratory_rate': v.respiratory_rate,
                'status': v.status,
                'recorded_at': v.recorded_at.isoformat()
            })

        return render_template('patient_detail.html',
            staff=staff,
            patient=patient,
            vitals=vitals,
            vitals_json=vitals_json,
            medications=medications,
            lab_reports=lab_reports,
            alerts=alerts,
            treatment_logs=treatment_logs
        )
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        app.logger.error('Error rendering patient_detail: %s', tb)
        return Response(tb, status=500, mimetype='text/plain')





@app.route('/alert/<int:alert_id>/acknowledge', methods=['POST'])
@staff_login_required
def acknowledge_alert(alert_id):
    staff = get_staff_user()
    alert = Alert.query.get_or_404(alert_id)
    
    alert.is_acknowledged = True
    alert.acknowledged_by_id = staff.id
    alert.acknowledged_at = datetime.now()
    db.session.commit()
    for recipient in alert_recipients(alert.patient):
        try:
            socketio.emit('alert_acknowledged', {
                'id': alert.id,
                'patient_id': alert.patient_id,
            }, to=f'staff_{recipient.id}')
        except Exception:
            log.exception(
                'Failed to notify staff %s that alert %s was acknowledged',
                recipient.id, alert.id)

    # If this was an AJAX request, return a JSON response so the client can update UI without reload
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
        return jsonify({'ok': True, 'alert_id': alert_id}), 200

    return redirect(request.referrer or url_for('dashboard'))


@app.route('/api/trigger-emergency-alert', methods=['POST'])
@staff_login_required
def trigger_emergency_alert():
    """Trigger a random patient emergency alert for demo/testing purposes"""
    try:
        staff = get_staff_user()
        
        # Get admitted patients
        patients = Patient.query.filter(Patient.status.in_(['admitted', 'icu', 'emergency'])).all()
        
        if not patients:
            return jsonify({'success': False, 'message': 'No patients available'}), 400
        
        # Pick a random patient
        import random
        selected_patient = random.choice(patients)
        
        # Create an alert
        alert = Alert(
            patient_id=selected_patient.id,
            alert_type='emergency',
            title='Emergency Alert Triggered',
            message=f'Abnormal vital signs detected for {selected_patient.full_name}. Immediate attention required.',
            severity='critical',
            is_acknowledged=False
        )
        db.session.add(alert)
        db.session.commit()
        from routing import _publish_alert
        _publish_alert(alert, selected_patient, alert_recipients(selected_patient))
        
        return jsonify({
            'success': True, 
            'message': 'Emergency alert triggered',
            'patient_name': selected_patient.full_name,
            'severity': 'critical',
            'alert_id': alert.id
        })
        
    except Exception as e:
        logging.error(f"Error triggering alert: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/medication/<int:medication_id>/delete', methods=['POST'])
@staff_login_required
@role_required('doctor', 'admin')
def delete_medication(medication_id):
    med = Medication.query.get_or_404(medication_id)
    med.is_active = False
    med.end_date = datetime.now()
    db.session.commit()
    flash('Medication removed successfully.', 'success')
    return redirect(request.referrer or url_for('dashboard'))


@app.route('/api/vitals/stream')
@staff_login_required
def vitals_stream():
    """Server-sent events feed of live vitals and open alerts.

    Read-only by design: vitals sampling and alert generation happen in the
    single background monitor, never per connected client.  Several dashboards
    open at once therefore do not multiply the alert volume.  Socket.IO is the
    primary transport; this endpoint exists as a no-dependency fallback.
    """
    import time
    from routing import PatientRouter

    def generate():
        while True:
            staff = get_staff_user()
            if staff is None:
                break
            # The connection itself counts as an open dashboard.
            mark_dashboard_open(staff.id)

            column = (Patient.assigned_doctor_id if staff.role == 'doctor'
                      else Patient.assigned_nurse_id)
            query = Patient.query.filter(Patient.status.in_(
                ('admitted', 'icu', 'emergency')))
            if staff.role in ('doctor', 'nurse'):
                query = query.filter(column == staff.id)
            elif staff.role != 'admin':
                break

            patients = query.order_by(Patient.room_number).all()
            vitals, alerts = [], []
            for patient in patients:
                latest = patient.latest_vitals
                if latest:
                    vitals.append({
                        'patient_id': patient.id,
                        'patient_name': patient.full_name,
                        'room': patient.room_number,
                        'bed': patient.bed_number,
                        'status': patient.status,
                        'vital_status': latest.status,
                        'heart_rate': latest.heart_rate,
                        'bp_systolic': latest.blood_pressure_systolic,
                        'bp_diastolic': latest.blood_pressure_diastolic,
                        'oxygen': latest.oxygen_saturation,
                        'temperature': latest.temperature,
                        'respiratory_rate': latest.respiratory_rate,
                        'recorded_at': latest.recorded_at.isoformat(),
                    })
                alerts.extend(
                    {'id': a.id, 'patient_id': patient.id,
                     'patient_name': patient.full_name, 'title': a.title,
                     'message': a.message, 'severity': a.severity,
                     'sequence_no': a.sequence_no}
                    for a in patient.alerts
                    if not a.is_acknowledged)

            # Strictly sequential, so the UI shows alerts one at a time.
            alerts.sort(key=lambda a: (a['sequence_no'] or 0))
            yield f"data: {json.dumps({'vitals': vitals, 'alerts': alerts, 'timestamp': datetime.now().isoformat()})}\n\n"
            time.sleep(5)

    return Response(generate(), mimetype='text/event-stream')


@app.route('/api/alerts/active')
@staff_login_required
def get_active_alerts():
    staff = get_staff_user()
    
    if staff.role == 'admin':
        return jsonify([])
    
    if not staff.is_on_duty:
        return jsonify([])
    
    alerts = Alert.query.filter_by(is_acknowledged=False).order_by(
        Alert.severity.desc(),
        Alert.created_at.desc()
    ).all()
    
    alerts_data = []
    for alert in alerts:
        patient = Patient.query.get(alert.patient_id)
        alerts_data.append({
            'id': alert.id,
            'patient_id': alert.patient_id,
            'patient_name': patient.full_name if patient else 'Unknown',
            'room': patient.room_number if patient else '',
            'bed': patient.bed_number if patient else '',
            'type': alert.alert_type,
            'severity': alert.severity,
            'title': alert.title,
            'message': alert.message,
            'created_at': alert.created_at.isoformat()
        })
    
    return jsonify(alerts_data)


@app.route('/api/patient/<int:patient_id>/vitals')
@staff_login_required
def get_patient_vitals(patient_id):
    vitals = VitalSign.query.filter_by(patient_id=patient_id).order_by(VitalSign.recorded_at.desc()).limit(20).all()
    
    vitals_data = []
    for vital in vitals:
        vitals_data.append({
            'id': vital.id,
            'heart_rate': vital.heart_rate,
            'bp_systolic': vital.blood_pressure_systolic,
            'bp_diastolic': vital.blood_pressure_diastolic,
            'oxygen': vital.oxygen_saturation,
            'temperature': vital.temperature,
            'respiratory_rate': vital.respiratory_rate,
            'status': vital.status,
            'recorded_at': vital.recorded_at.isoformat()
        })
    
    return jsonify(vitals_data)


@app.route('/init-data')
def init_data():
    # Demo data initialization is disabled in production
    flash('This feature has been disabled.', 'warning')
    return redirect(url_for('index'))


@app.route('/credentials')
def view_credentials():
    return render_template('credentials.html')


@app.context_processor
def utility_processor():
    return {
        'now': datetime.now(),
        'get_staff_user': get_staff_user
    }


@app.route('/patient/<int:patient_id>/risk-analysis')
@staff_login_required
def patient_risk_analysis(patient_id):
    staff = get_staff_user()
    patient = Patient.query.get_or_404(patient_id)
    
    analysis = risk_predictor.analyze_patient_risk(patient_id)
    
    risk_assessment = RiskAssessment(
        patient_id=patient_id,
        risk_level=analysis['risk_level'],
        risk_score=analysis['risk_score'],
        risk_factors=json.dumps(analysis.get('risk_factors', [])),
        predictions=json.dumps(analysis.get('predictions', []))
    )
    db.session.add(risk_assessment)
    db.session.commit()
    
    past_assessments = RiskAssessment.query.filter_by(patient_id=patient_id).order_by(
        RiskAssessment.assessed_at.desc()
    ).limit(10).all()
    
    return render_template('risk_analysis.html',
        staff=staff,
        patient=patient,
        analysis=analysis,
        past_assessments=past_assessments
    )


@app.route('/api/risk-analysis/<int:patient_id>')
@staff_login_required
def api_risk_analysis(patient_id):
    analysis = risk_predictor.analyze_patient_risk(patient_id)
    return jsonify(analysis)


@app.route('/api/risk-analysis/all')
@staff_login_required
def api_all_risk_analysis():
    from services import analyze_all_patients
    return jsonify(analyze_all_patients())


@app.route('/medication/<int:patient_id>/schedule', methods=['GET', 'POST'])
@staff_login_required
@role_required('doctor', 'nurse', 'admin')
def medication_schedule(patient_id):
    staff = get_staff_user()
    patient = Patient.query.get_or_404(patient_id)
    
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        dosage = request.form.get('dosage', '').strip()
        frequency = request.form.get('frequency', '').strip()
        route = request.form.get('route', '').strip()
        notes = request.form.get('notes', '').strip()
        
        if name and dosage and frequency:
            freq_hours = {
                'Once daily': 24,
                'Twice daily': 12,
                'Three times daily': 8,
                'Four times daily': 6,
                'Every 4 hours': 4,
                'Every 6 hours': 6,
                'Every 8 hours': 8,
                'Every 12 hours': 12,
                'As needed': 0
            }.get(frequency, 24)
            
            from datetime import timedelta
            next_due = datetime.now() + timedelta(hours=freq_hours) if freq_hours > 0 else None
            
            medication = Medication(
                patient_id=patient_id,
                name=name,
                dosage=dosage,
                frequency=frequency,
                route=route,
                start_date=datetime.now(),
                next_due=next_due,
                prescribed_by_id=staff.id if staff.role == 'doctor' else None,
                notes=notes,
                is_active=True
            )
            db.session.add(medication)
            db.session.commit()
            
            flash(f'Medication "{name}" scheduled successfully.', 'success')
            return redirect(url_for('patient_medications', patient_id=patient_id))
        else:
            flash('Please fill in all required fields.', 'danger')
    
    return render_template('medication_schedule.html', staff=staff, patient=patient)


@app.route('/patient/<int:patient_id>/medications')
@staff_login_required
def patient_medications(patient_id):
    staff = get_staff_user()
    patient = Patient.query.get_or_404(patient_id)
    
    medications = Medication.query.filter_by(patient_id=patient_id, is_active=True).all()
    past_medications = Medication.query.filter_by(patient_id=patient_id, is_active=False).limit(10).all()
    
    administrations = MedicationAdministration.query.filter_by(patient_id=patient_id).order_by(
        MedicationAdministration.administered_at.desc()
    ).limit(30).all()
    
    return render_template('patient_medications.html',
        staff=staff,
        patient=patient,
        medications=medications,
        past_medications=past_medications,
        administrations=administrations
    )


@app.route('/medication/<int:medication_id>/administer', methods=['POST'])
@staff_login_required
@role_required('nurse', 'admin')
def administer_medication(medication_id):
    staff = get_staff_user()
    medication = Medication.query.get_or_404(medication_id)
    
    notes = request.form.get('notes', '').strip()
    status = request.form.get('status', 'administered')
    
    administration = MedicationAdministration(
        medication_id=medication_id,
        patient_id=medication.patient_id,
        administered_by_id=staff.id,
        scheduled_time=medication.next_due or datetime.now(),
        dosage_given=medication.dosage,
        route=medication.route,
        status=status,
        notes=notes
    )
    db.session.add(administration)
    
    medication.last_administered = datetime.now()
    
    freq_hours = {
        'Once daily': 24,
        'Twice daily': 12,
        'Three times daily': 8,
        'Four times daily': 6,
        'Every 4 hours': 4,
        'Every 6 hours': 6,
        'Every 8 hours': 8,
        'Every 12 hours': 12,
        'As needed': 0
    }.get(medication.frequency, 24)
    
    from datetime import timedelta
    if freq_hours > 0:
        medication.next_due = datetime.now() + timedelta(hours=freq_hours)
    
    db.session.commit()
    
    flash(f'Medication administration recorded.', 'success')
    return redirect(url_for('patient_medications', patient_id=medication.patient_id))


@app.route('/shifts')
@staff_login_required
def shift_management():
    staff = get_staff_user()
    
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    from datetime import timedelta
    tomorrow = today + timedelta(days=1)
    
    todays_shifts = Shift.query.filter(
        Shift.start_time >= today,
        Shift.start_time < tomorrow
    ).order_by(Shift.start_time).all()
    
    _TEST_STAFF_IDS = ['DOC_RT', 'NRS_RT', 'NRS_ICU', 'NRS_ER']
    on_duty_staff = StaffMember.query.filter(
        StaffMember.is_on_duty == True, StaffMember.is_active == True,
        ~StaffMember.staff_id.in_(_TEST_STAFF_IDS)
    ).all()
    all_staff = StaffMember.query.filter(
        StaffMember.role.in_(['doctor', 'nurse']), StaffMember.is_active == True,
        ~StaffMember.staff_id.in_(_TEST_STAFF_IDS)
    ).all()
    
    pending_handoffs = ShiftHandoff.query.filter_by(acknowledged=False).order_by(
        ShiftHandoff.handoff_time.desc()
    ).all()
    
    return render_template('shifts.html',
        staff=staff,
        todays_shifts=todays_shifts,
        on_duty_staff=on_duty_staff,
        all_staff=all_staff,
        pending_handoffs=pending_handoffs
    )


@app.route('/shifts/create', methods=['POST'])
@staff_login_required
@role_required('admin')
def create_shift():
    staff_member_id = request.form.get('staff_id', type=int)
    shift_type = request.form.get('shift_type', '')
    department = request.form.get('department', '')
    date_str = request.form.get('date', '')
    
    if not staff_member_id or not shift_type or not date_str:
        flash('Please fill in all required fields.', 'danger')
        return redirect(url_for('shift_management'))
    
    shift_times = {
        'morning': (7, 15),
        'afternoon': (15, 23),
        'night': (23, 7)
    }
    
    from datetime import timedelta
    date = datetime.strptime(date_str, '%Y-%m-%d')
    start_hour, end_hour = shift_times.get(shift_type, (7, 15))
    
    start_time = date.replace(hour=start_hour, minute=0, second=0)
    if end_hour < start_hour:
        end_time = (date + timedelta(days=1)).replace(hour=end_hour, minute=0, second=0)
    else:
        end_time = date.replace(hour=end_hour, minute=0, second=0)
    
    shift = Shift(
        staff_id=staff_member_id,
        shift_type=shift_type,
        start_time=start_time,
        end_time=end_time,
        department=department,
        is_active=True
    )
    db.session.add(shift)
    db.session.commit()
    
    flash('Shift created successfully.', 'success')
    return redirect(url_for('shift_management'))


@app.route('/shifts/<int:shift_id>/check-in', methods=['POST'])
@staff_login_required
def shift_check_in(shift_id):
    staff = get_staff_user()
    shift = Shift.query.get_or_404(shift_id)
    
    if shift.staff_id != staff.id and staff.role != 'admin':
        flash('You can only check in to your own shift.', 'danger')
        return redirect(url_for('shift_management'))
    
    shift.checked_in_at = datetime.now()
    
    staff_member = StaffMember.query.get(shift.staff_id)
    if staff_member:
        staff_member.is_on_duty = True
    
    db.session.commit()
    
    flash('Checked in successfully.', 'success')
    return redirect(url_for('shift_management'))


@app.route('/shifts/<int:shift_id>/check-out', methods=['POST'])
@staff_login_required
def shift_check_out(shift_id):
    staff = get_staff_user()
    shift = Shift.query.get_or_404(shift_id)
    
    if shift.staff_id != staff.id and staff.role != 'admin':
        flash('You can only check out from your own shift.', 'danger')
        return redirect(url_for('shift_management'))
    
    shift.checked_out_at = datetime.now()
    shift.is_active = False
    
    staff_member = StaffMember.query.get(shift.staff_id)
    if staff_member:
        staff_member.is_on_duty = False
    
    db.session.commit()
    
    flash('Checked out successfully.', 'success')
    return redirect(url_for('shift_management'))


@app.route('/handoff/create', methods=['GET', 'POST'])
@staff_login_required
def create_handoff():
    staff = get_staff_user()
    
    if request.method == 'POST':
        incoming_staff_id = request.form.get('incoming_staff_id', type=int)
        patient_id = request.form.get('patient_id', type=int) or None
        summary = request.form.get('summary', '').strip()
        critical_notes = request.form.get('critical_notes', '').strip()
        pending_tasks = request.form.get('pending_tasks', '').strip()
        
        if not incoming_staff_id or not summary:
            flash('Please provide incoming staff and summary.', 'danger')
            return redirect(url_for('create_handoff'))
        
        handoff = ShiftHandoff(
            outgoing_staff_id=staff.id,
            incoming_staff_id=incoming_staff_id,
            patient_id=patient_id,
            summary=summary,
            critical_notes=critical_notes,
            pending_tasks=pending_tasks
        )
        db.session.add(handoff)
        db.session.commit()
        
        flash('Handoff report created successfully.', 'success')
        return redirect(url_for('shift_management'))
    
    incoming_staff_list = StaffMember.query.filter(
        StaffMember.role == staff.role,
        StaffMember.id != staff.id,
        StaffMember.is_active == True
    ).all()
    
    if staff.role == 'doctor':
        patients = Patient.query.filter_by(assigned_doctor_id=staff.id).filter(Patient.status != 'discharged').all()
    elif staff.role == 'nurse':
        patients = Patient.query.filter_by(assigned_nurse_id=staff.id).filter(Patient.status != 'discharged').all()
    else:
        patients = Patient.query.filter(Patient.status != 'discharged').all()
    
    return render_template('handoff_create.html',
        staff=staff,
        incoming_staff_list=incoming_staff_list,
        patients=patients
    )


@app.route('/handoff/<int:handoff_id>/acknowledge', methods=['POST'])
@staff_login_required
def acknowledge_handoff(handoff_id):
    staff = get_staff_user()
    handoff = ShiftHandoff.query.get_or_404(handoff_id)
    
    if handoff.incoming_staff_id != staff.id:
        flash('You can only acknowledge handoffs addressed to you.', 'danger')
        return redirect(url_for('shift_management'))
    
    handoff.acknowledged = True
    handoff.acknowledged_at = datetime.now()
    db.session.commit()
    
    flash('Handoff acknowledged.', 'success')
    return redirect(url_for('shift_management'))


@app.route('/patient/<int:patient_id>/history')
@staff_login_required
def patient_history(patient_id):
    staff = get_staff_user()
    if not staff:
        flash('You must be logged in as staff to access this page.', 'danger')
        return redirect(url_for('staff_login'))
    patient = Patient.query.get_or_404(patient_id)
    
    vitals = VitalSign.query.filter_by(patient_id=patient_id).order_by(VitalSign.recorded_at.desc()).limit(100).all()
    treatments = TreatmentLog.query.filter_by(patient_id=patient_id).order_by(TreatmentLog.performed_at.desc()).all()
    notes = DoctorNote.query.filter_by(patient_id=patient_id).order_by(DoctorNote.created_at.desc()).all()
    medications = MedicationAdministration.query.filter_by(patient_id=patient_id).order_by(
        MedicationAdministration.administered_at.desc()
    ).all()
    alerts = Alert.query.filter_by(patient_id=patient_id).order_by(Alert.created_at.desc()).limit(50).all()
    risk_assessments = RiskAssessment.query.filter_by(patient_id=patient_id).order_by(
        RiskAssessment.assessed_at.desc()
    ).limit(20).all()
    
    return render_template('patient_history.html',
        staff=staff,
        patient=patient,
        vitals=vitals,
        treatments=treatments,
        notes=notes,
        medications=medications,
        alerts=alerts,
        risk_assessments=risk_assessments
    )





@app.route('/patient/<int:patient_id>/add-treatment', methods=['GET', 'POST'])
@staff_login_required
def add_treatment(patient_id):
    staff = get_staff_user()
    patient = Patient.query.get_or_404(patient_id)
    
    if request.method == 'POST':
        treatment_type = request.form.get('treatment_type', '').strip()
        description = request.form.get('description', '').strip()
        notes = request.form.get('notes', '').strip()
        
        if treatment_type and description:
            treatment = TreatmentLog(
                patient_id=patient_id,
                staff_id=staff.id,
                treatment_type=treatment_type,
                description=description,
                notes=notes
            )
            db.session.add(treatment)
            db.session.commit()
            
            flash('Treatment logged successfully.', 'success')
            return redirect(url_for('patient_history', patient_id=patient_id))
        else:
            flash('Please fill in all required fields.', 'danger')
    
    return render_template('add_treatment.html', staff=staff, patient=patient)


# ===== UNIVERSAL PATIENT PORTAL =====

def get_logged_in_patient():
    if 'patient_id' in session:
        return Patient.query.get(session['patient_id'])
    return None

def patient_login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'patient_id' not in session:
            flash('Please log in to access this page.', 'warning')
            return redirect(url_for('patient_login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/patient-login', methods=['GET', 'POST'])
def patient_login():
    """Universal patient login portal (Current & Discharged)"""
    if request.method == 'POST':
        patient_id_str = request.form.get('patient_id', '').strip().upper()
        phone = request.form.get('phone', '').strip()

        # Normalize phone by removing non-digit characters for matching
        def norm_phone(p):
            return ''.join([c for c in (p or '') if c.isdigit()])

        norm_input_phone = norm_phone(phone)

        # Query patient by ID (any status)
        patient = Patient.query.filter_by(patient_id=patient_id_str).first()
        
        # Verify phone matches (normalized)
        if patient and patient.phone:
            if norm_phone(patient.phone) == norm_input_phone:
                session['patient_id'] = patient.id
                flash(f'Welcome back, {patient.first_name}!', 'success')
                return redirect(url_for('patient_portal_dashboard'))
        
        # No valid match
        flash('Invalid Patient ID or phone number. Please check and try again.', 'danger')
    
    return render_template('patient_login.html')


@app.route('/discharged-portal')
@app.route('/discharged-dashboard')
@patient_login_required
def discharged_dashboard():
    """Redirect to the unified patient portal dashboard"""
    return redirect(url_for('patient_portal_dashboard'))

@app.route('/discharged-logout')
def discharged_logout():
    """Alias for patient logout"""
    return redirect(url_for('patient_logout'))


@app.route('/patient-portal/dashboard')
@patient_login_required
def patient_portal_dashboard():
    """Main dashboard for the patient portal (Universal for all patient statuses)"""
    patient = get_logged_in_patient()
    if not patient:
        session.pop('patient_id', None)
        return redirect(url_for('patient_login'))
    
    # helper for upcoming appointments
    upcoming_appointments_count = AppointmentRequest.query.filter(
        AppointmentRequest.patient_id == patient.id,
        AppointmentRequest.status.in_(['pending', 'confirmed']),
        AppointmentRequest.preferred_date >= datetime.now().date()
    ).count()

    # helper for active meds
    active_meds_count = Medication.query.filter_by(patient_id=patient.id, is_active=True).count()

    # helper for lab reports (assuming LabReport model exists, if not catch error)
    lab_reports_count = 0
    try:
        lab_reports_count = LabReport.query.filter_by(patient_id=patient.id).count()
    except Exception:
        pass # LabReport might not exist yet

    # latest vital
    latest_vital = patient.vitals.order_by(VitalSign.recorded_at.desc()).first()

    # Recent Activity (Mockup aggregation)
    recent_activity = []
    
    # 1. Recent Meds
    recent_meds = Medication.query.filter_by(patient_id=patient.id).order_by(Medication.start_date.desc()).limit(3).all()
    for m in recent_meds:
        recent_activity.append({
            'type': 'medication',
            'title': f'Started {m.name}',
            'description': f'Dosage: {m.dosage}',
            'time': m.start_date.strftime('%Y-%m-%d'),
            'timestamp': datetime.combine(m.start_date, datetime.min.time())
        })

    # 2. Recent Vitals
    recent_vitals = patient.vitals.order_by(VitalSign.recorded_at.desc()).limit(3).all()
    for v in recent_vitals:
        recent_activity.append({
            'type': 'vital',
            'title': 'Vitals Recorded',
            'description': f'BP: {v.blood_pressure_systolic}/{v.blood_pressure_diastolic}',
            'time': v.recorded_at.strftime('%Y-%m-%d %H:%M'),
            'timestamp': v.recorded_at
        })
    
    # Sort by timestamp desc
    recent_activity.sort(key=lambda x: x['timestamp'], reverse=True)
    recent_activity = recent_activity[:5]

    return render_template('patient_portal_dashboard.html',
        patient=patient,
        active_meds_count=active_meds_count,
        upcoming_appointments_count=upcoming_appointments_count,
        latest_vital=latest_vital,
        lab_reports_count=lab_reports_count,
        recent_activity=recent_activity
    )

@app.route('/patient-portal/history')
@patient_login_required
def patient_portal_history():
    patient = get_logged_in_patient()
    notes = DoctorNote.query.filter_by(patient_id=patient.id).order_by(DoctorNote.created_at.desc()).all()
    # treatments could be added if we create a model, for now just notes
    return render_template('patient_portal_history.html', patient=patient, notes=notes, treatments=[])

@app.route('/patient-portal/medications')
@patient_login_required
def patient_portal_medications():
    patient = get_logged_in_patient()
    medications = Medication.query.filter_by(patient_id=patient.id).order_by(Medication.start_date.desc()).all()
    return render_template('patient_portal_medications.html', patient=patient, medications=medications)

@app.route('/patient-portal/appointments')
@patient_login_required
def patient_portal_appointments():
    patient = get_logged_in_patient()
    appointments = AppointmentRequest.query.filter_by(patient_id=patient.id).order_by(AppointmentRequest.requested_at.desc()).all()
    doctors = StaffMember.query.filter_by(role='doctor').all()
    return render_template('patient_portal_appointments.html', patient=patient, appointments=appointments, doctors=doctors)

@app.route('/patient-portal/chat')
@patient_login_required
def patient_portal_chat():
    patient = get_logged_in_patient()
    chat_history = ChatMessage.query.filter_by(patient_id=patient.id).order_by(ChatMessage.created_at.asc()).all()
    return render_template('patient_portal_chat.html', patient=patient, chat_history=chat_history)

@app.route('/patient-logout')
def patient_logout():
    session.pop('patient_id', None)
    flash('You have been logged out.', 'info')
    return redirect(url_for('patient_login'))


# ===== PATIENT API ENDPOINTS =====

@app.route('/api/patient/<int:patient_id>/chat', methods=['POST'])
def patient_chat(patient_id):
    """Chat API with multi-language support using deep-translator"""
    
    # Authorization
    staff = get_staff_user()
    if not staff and session.get('patient_id') != patient_id:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.get_json() or {}
    user_message = data.get('message', '').strip()
    new_chat_flag = data.get('new_chat', False)
    language = data.get('language', 'en').lower()
    
    if not user_message:
        return jsonify({'error': 'Message cannot be empty'}), 400
    
    patient = Patient.query.get_or_404(patient_id)
    
    # Translate user message to English for better AI processing if not already in English
    user_message_for_ai = translate_text(user_message, language, 'en')
    
    # Store original user message
    user_msg = ChatMessage(patient_id=patient_id, role='patient', message=user_message, language=language)
    db.session.add(user_msg)
    db.session.commit()
    
    # Try Groq first if configured
    ai_response = None
    try:
        recent_chat = (ChatMessage.query.filter_by(patient_id=patient_id)
                       .order_by(ChatMessage.created_at.desc()).limit(7).all())
        recent_chat.reverse()

        lang_name = {
            'en': 'English', 'hi': 'Hindi', 'ta': 'Tamil',
            'te': 'Telugu', 'ml': 'Malayalam'
        }.get(language, 'English')

        context = f"Patient: {patient.full_name}. Diagnosis: {patient.diagnosis}. "
        lv = patient.vitals.order_by(VitalSign.recorded_at.desc()).first()
        if lv:
            context += (
                f"Latest Vitals: HR {lv.heart_rate}, BP "
                f"{lv.blood_pressure_systolic}/{lv.blood_pressure_diastolic}, "
                f"SpO2 {lv.oxygen_saturation}%. ")

        active_meds = Medication.query.filter_by(
            patient_id=patient_id, is_active=True).all()
        if active_meds:
            med_list = [
                f"{m.name} ({m.dosage}, {m.frequency})" for m in active_meds]
            context += f"Active Meds: {', '.join(med_list)}. "

        context += (
            f"Reply in {lang_name}. Be concise, empathetic. "
            "If emergency suspected, advise ER immediately.")

        messages = [{'role': 'system', 'content': context}]
        for message in recent_chat:
            if message.id == user_msg.id:
                continue
            role = 'user' if message.role == 'patient' else message.role
            if role in ('user', 'assistant'):
                messages.append({'role': role, 'content': message.message})
        messages.append({'role': 'user', 'content': user_message_for_ai})
        ai_response = generate_ai_response(messages)
    except Exception:
        log.warning('Groq chatbot request failed; using fallback')
    
    # Use the local fallback if Groq is unavailable or fails
    if not ai_response:
        ai_response = generate_fallback_response(patient, user_message_for_ai, language)
    
    # Translate AI response back to target language if needed
    final_response = translate_text(ai_response, 'en', language)
    
    try:
        ai_msg = ChatMessage(patient_id=patient_id, role='assistant', message=final_response, language=language)
        db.session.add(ai_msg)
        db.session.commit()
        return jsonify({'role': 'assistant', 'message': final_response, 'language': language, 'message_id': ai_msg.id})
    except Exception:
        db.session.rollback()
        log.exception('Unable to persist chatbot response')
        return jsonify({'role': 'assistant', 'message': final_response, 'language': language})

@app.route('/api/chat/feedback/<int:message_id>', methods=['POST'])
def chat_feedback(message_id):
    """Record helpfulness feedback for a chat message"""
    # Auth check (staff or patient owner)
    staff = get_staff_user()
    data = request.get_json() or {}
    
    msg = ChatMessage.query.get_or_404(message_id)
    
    # Simple ownership check
    if not staff and session.get('patient_id') != msg.patient_id:
        return jsonify({'error': 'Unauthorized'}), 401
        
    is_helpful = data.get('is_helpful')
    if is_helpful is not None:
        msg.is_helpful = bool(is_helpful)
        
    feedback_text = data.get('feedback_text')
    if feedback_text:
        msg.feedback_text = feedback_text
        
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/discharged/clear-history', methods=['POST'])
@patient_login_required
def api_discharged_clear_history():
    patient = get_logged_in_patient()
    if not patient:
        return jsonify({'error': 'Unauthorized'}), 401
    
    ChatMessage.query.filter_by(patient_id=patient.id).delete()
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/api/discharged/summary')
@patient_login_required
def api_discharged_summary():
    """Get a detailed health summary for the discharged patient"""
    patient = get_logged_in_patient()
    if not patient:
        return jsonify({'error': 'Unauthorized'}), 401
    
    # 1. Basic Info
    summary = {
        'name': patient.full_name,
        'patient_id': patient.patient_id,
        'mobile': patient.phone,
        'age': patient.age,
        'admission_date': patient.admission_date.isoformat() if patient.admission_date else None,
        'discharge_date': patient.discharge_date.isoformat() if patient.discharge_date else None,
        'address': patient.address,
        'health_issue': patient.diagnosis,
    }
    
    # 2. Lab Results
    try:
        labs = LabReport.query.filter_by(patient_id=patient.id).order_by(LabReport.reported_at.desc()).all()
        summary['lab_results'] = [{
            'test_name': l.test_name,
            'result': l.result,
            'units': l.units,
            'normal_range': l.normal_range,
            'reported_at': l.reported_at.isoformat() if l.reported_at else None
        } for l in labs]
    except:
        summary['lab_results'] = []
        
    # 3. Medications
    meds = Medication.query.filter_by(patient_id=patient.id, is_active=True).all()
    summary['medical_prescription'] = [{
        'name': m.name,
        'dosage': m.dosage,
        'frequency': m.frequency,
        'route': m.route
    } for m in meds]
    
    # 4. Risk and Predictions
    risk = RiskAssessment.query.filter_by(patient_id=patient.id).order_by(RiskAssessment.assessed_at.desc()).first()
    if risk:
        summary['risk_level'] = risk.risk_level
        summary['risk_score'] = risk.risk_score
        summary['predictions'] = json.loads(risk.predictions) if risk.predictions else []
    else:
        summary['risk_level'] = 'low'
        summary['risk_score'] = 0
        summary['predictions'] = ["Continue standard recovery protocol.", "Monitor vitals daily."]
        
    return jsonify(summary)

@app.route('/api/patient/<int:patient_id>/clear-history', methods=['POST'])
def patient_clear_history(patient_id):
    staff = get_staff_user()
    if not staff and session.get('patient_id') != patient_id:
        return jsonify({'error': 'Unauthorized'}), 401
    
    ChatMessage.query.filter_by(patient_id=patient_id).delete()
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/api/patient/<int:patient_id>/book-appointment', methods=['POST'])
def patient_book_appointment(patient_id):
    """Book appointment with AI-powered intelligent doctor allocation"""
    
    staff = get_staff_user()
    patient_in_session = session.get('patient_id')
    if not staff and patient_in_session != patient_id:
        return jsonify({'error': 'Unauthorized'}), 401
        
    data = request.get_json() or {}
    try:
        pref_date = datetime.fromisoformat(data.get('preferred_date')) if data.get('preferred_date') else None
    except:
        pref_date = None
    
    # Create appointment with all new fields
    appt = AppointmentRequest(
        patient_id=patient_id,
        preferred_date=pref_date,
        preferred_time=data.get('preferred_time'),
        doctor_id=data.get('doctor_id'),
        notes=data.get('notes', ''),
        appointment_type=data.get('appointment_type', 'routine'),
        department=data.get('department', ''),
        language_preference=data.get('language', 'en'),
        urgency=data.get('urgency', 'normal'),
        source=data.get('source', 'patient_portal'),
        status='pending'
    )
    db.session.add(appt)
    db.session.commit()
    
    # Send booking notification
    notification_service.send_booking_notification(appt)
    
    # SPECIAL: Send notification to admin phone number for discharged patients
    patient = Patient.query.get(patient_id)
    if patient and patient.status == 'discharged':
        admin_phone = "+919700443157"
        msg = (
            f"Dear Admin,\n"
            f"A new follow-up appointment has been successfully booked at Narayana Hospital, Nellore by a discharged patient.\n\n"
            f"Patient: {patient.full_name} ({patient.patient_id})\n"
            f"Appointment No.: APT{appt.id:04d}\n"
            f"Department: {appt.department or 'General Medicine'}\n"
            f"Date: {data.get('preferred_date')}\n"
            f"Time: {data.get('preferred_time')}\n"
            f"Location: Narayana Hospital, Nellore\n\n"
            f"Please ensure a doctor is allocated if not already done.\n\n"
            f"Thank you!"
        )
        notification_service._send_sms(admin_phone, msg)
    
    # Try intelligent AI doctor allocation if no doctor specified
    allocated_doctor = None
    if not appt.doctor_id:
        if appt.appointment_type == 'emergency':
            # For emergency, use basic fallback routing (faster)
            allocated_doctor = appointment_router._fallback_routing(appt)
        else:
            # Use AI-powered routing for other appointments
            allocated_doctor = appointment_router.route_appointment(appt)
        
        if allocated_doctor:
            appt.status = 'confirmed'
            db.session.commit()
    
    # Send confirmation notification only if allocated
    if allocated_doctor:
        notification_service.send_appointment_confirmation(appt, allocated_doctor)
    
    return jsonify({
        'ok': True, 
        'appointment_id': appt.id,
        'status': appt.status,
        'allocated_by_system': appt.allocated_by_system,
        'allocated_doctor': f"Dr. {allocated_doctor.first_name} {allocated_doctor.last_name}" if allocated_doctor else None,
        'routing_score': float(appt.routing_score) if appt.routing_score else None
    })



@app.route('/api/discharged/book-appointment', methods=['POST'])
@patient_login_required
def api_discharged_book_appointment():
    """Book appointment for discharged patients"""
    patient = get_logged_in_patient()
    if not patient:
         return jsonify({'error': 'Patient not found'}), 404
         
    data = request.get_json() or {}
    try:
        pref_date = datetime.fromisoformat(data.get('preferred_date')) if data.get('preferred_date') else None
    except:
        pref_date = None
        
    # Create appointment request
    appt = AppointmentRequest(
        patient_id=patient.id,
        preferred_date=pref_date,
        preferred_time=data.get('preferred_time'),
        notes=data.get('notes', ''),
        source='discharged_portal',
        appointment_type='follow-up',
        urgency='normal',
        status='pending'
    )
    db.session.add(appt)
    db.session.commit()
    
    # Send booking notification to patient
    notification_service.send_booking_notification(appt)
    
    # SPECIAL: Send notification to admin phone number as requested
    admin_phone = "+919700443157"
    msg = (
        f"Dear Admin,\n"
        f"A new follow-up appointment has been successfully booked at Narayana Hospital, Nellore by a discharged patient.\n\n"
        f"Patient: {patient.full_name} ({patient.patient_id})\n"
        f"Appointment No.: APT{appt.id:04d}\n"
        f"Department: Follow-up\n"
        f"Date: {data.get('preferred_date')}\n"
        f"Time: {data.get('preferred_time')}\n"
        f"Location: Narayana Hospital, Nellore\n\n"
        f"Please ensure a doctor is allocated if not already done.\n\n"
        f"Thank you!"
    )
    notification_service._send_sms(admin_phone, msg)
    
    # Basic AI allocation
    allocated_doctor = appointment_router.route_appointment(appt)
    if allocated_doctor:
        appt.status = 'confirmed'
        db.session.commit()
        notification_service.send_appointment_confirmation(appt, allocated_doctor)
        
    return jsonify({
        'ok': True,
        'appointment_id': appt.id,
        'status': appt.status,
        'allocated_doctor': f"Dr. {allocated_doctor.first_name} {allocated_doctor.last_name}" if allocated_doctor else None
    })

# ===== NEW APPOINTMENT SYSTEM ENDPOINTS =====

@app.route('/api/appointment/translations/<language>', methods=['GET'])
def get_appointment_translations(language='en'):
    """Get appointment form translations"""
    from appointment_routing import get_appointment_translations
    
    translations = get_appointment_translations(language)
    return jsonify(translations)


@app.route('/api/patient/<int:patient_id>/medical-records', methods=['GET'])
@patient_login_required
def get_patient_medical_records(patient_id):
    """Get comprehensive medical records including notes, treatments, vitals, medications, alerts"""
    
    # Verify authorization
    patient = Patient.query.get_or_404(patient_id)
    if session.get('patient_id') != patient_id:
        staff = get_staff_user()
        if not staff or (staff.id != patient.assigned_doctor_id and staff.id != patient.assigned_nurse_id):
            return jsonify({'error': 'Unauthorized'}), 401
    
    # Get doctor notes
    doctor_notes = DoctorNote.query.filter_by(patient_id=patient_id).order_by(
        DoctorNote.created_at.desc()
    ).all()
    
    notes_data = [{
        'id': note.id,
        'type': note.note_type,
        'doctor': note.doctor.full_name if note.doctor else 'Unknown',
        'subjective': note.subjective,
        'objective': note.objective,
        'assessment': note.assessment,
        'plan': note.plan,
        'date': note.created_at.isoformat()
    } for note in doctor_notes]
    
    # Get treatment logs
    treatments = TreatmentLog.query.filter_by(patient_id=patient_id).order_by(
        TreatmentLog.performed_at.desc()
    ).all()
    
    treatments_data = [{
        'id': treatment.id,
        'type': treatment.treatment_type,
        'description': treatment.description,
        'notes': treatment.notes,
        'staff': treatment.staff.full_name if treatment.staff else 'Unknown',
        'date': treatment.performed_at.isoformat()
    } for treatment in treatments]
    
    # Get vitals history (last 30)
    vitals = VitalSign.query.filter_by(patient_id=patient_id).order_by(
        VitalSign.recorded_at.desc()
    ).limit(30).all()
    
    vitals_data = [{
        'id': vital.id,
        'heart_rate': vital.heart_rate,
        'blood_pressure_systolic': vital.blood_pressure_systolic,
        'blood_pressure_diastolic': vital.blood_pressure_diastolic,
        'oxygen_saturation': vital.oxygen_saturation,
        'temperature': vital.temperature,
        'respiratory_rate': vital.respiratory_rate,
        'status': vital.status,
        'date': vital.recorded_at.isoformat()
    } for vital in vitals]
    
    # Get medications
    medications = Medication.query.filter_by(patient_id=patient_id).all()
    
    meds_data = [{
        'id': med.id,
        'name': med.name,
        'dosage': med.dosage,
        'frequency': med.frequency,
        'route': med.route,
        'is_active': med.is_active,
        'start_date': med.start_date.isoformat() if med.start_date else None,
        'end_date': med.end_date.isoformat() if med.end_date else None,
        'prescribed_by': med.prescribed_by.full_name if med.prescribed_by else 'Unknown',
        'notes': med.notes
    } for med in medications]
    
    # Get alerts
    alerts = Alert.query.filter_by(patient_id=patient_id).order_by(
        Alert.created_at.desc()
    ).limit(20).all()
    
    alerts_data = [{
        'id': alert.id,
        'type': alert.alert_type,
        'severity': alert.severity,
        'title': alert.title,
        'message': alert.message,
        'is_acknowledged': alert.is_acknowledged,
        'date': alert.created_at.isoformat()
    } for alert in alerts]
    
    return jsonify({
        'patient': {
            'id': patient.id,
            'name': patient.full_name,
            'diagnosis': patient.diagnosis,
            'status': patient.status,
            'room': patient.room_number,
            'bed': patient.bed_number
        },
        'doctor_notes': notes_data,
        'treatments': treatments_data,
        'vitals_history': vitals_data,
        'medications': meds_data,
        'alerts': alerts_data
    })


@app.route('/api/landing-page/book-appointment', methods=['POST'])
def landing_page_book_appointment():
    """Book appointment directly from landing page (for new/guest patients)"""
    
    data = request.get_json() or {}
    
    # For landing page bookings, we might not have a patient ID yet
    # Try to find or create patient
    phone = data.get('phone', '').strip()
    email = data.get('email', '').strip()
    first_name = data.get('first_name', '').strip()
    last_name = data.get('last_name', '').strip()
    
    # Normalize phone number - ensure +91 prefix for Indian numbers
    if phone and not phone.startswith('+'):
        phone = '+91' + phone.lstrip('0')
    
    if not (phone or email) or not (first_name and last_name):
        return jsonify({'error': 'Missing required fields'}), 400
    
    # Try to find existing patient (check both with and without +91)
    patient = None
    if phone:
        patient = Patient.query.filter_by(phone=phone).first()
        if not patient:
            # Try without +91 prefix
            phone_without_prefix = phone.replace('+91', '')
            patient = Patient.query.filter_by(phone=phone_without_prefix).first()
    if not patient and email:
        patient = Patient.query.filter_by(email=email).first()
    
    # If patient doesn't exist, create new patient record
    if not patient:
        try:
            # Generate patient ID robustly
            # Find all patient IDs that follow the PAT000000 format
            import re
            all_patients = Patient.query.filter(Patient.patient_id.like('PAT%')).all()
            max_num = 0
            for p in all_patients:
                match = re.search(r'PAT(\d+)', p.patient_id)
                if match:
                    num = int(match.group(1))
                    if num > max_num:
                        max_num = num
            
            patient_num = max_num + 1
            
            patient = Patient(
                patient_id=f'PAT{patient_num:04d}',
                first_name=first_name,
                last_name=last_name,
                phone=phone,
                email=email,
                date_of_birth=datetime.strptime(data.get('dob', '2000-01-01'), '%Y-%m-%d').date() if data.get('dob') else datetime(2000, 1, 1).date(),
                gender=data.get('gender', 'Other'),
                status='inquiry',
                department=data.get('department', 'General Medicine')
            )
            db.session.add(patient)
            db.session.commit()
        except Exception as e:
            logging.error(f"Error creating patient: {str(e)}")
            return jsonify({'error': 'Could not create patient record'}), 400
    
    # Create appointment request
    try:
        pref_date = datetime.fromisoformat(data.get('preferred_date')) if data.get('preferred_date') else None
    except:
        pref_date = None
    
    appt = AppointmentRequest(
        patient_id=patient.id,
        preferred_date=pref_date,
        preferred_time=data.get('preferred_time'),
        notes=data.get('reason', ''),
        appointment_type=data.get('appointment_type', 'consultation'),
        department=data.get('department', 'General Medicine'),
        language_preference=data.get('language', 'en'),
        urgency=data.get('urgency', 'normal'),
        source='landing_page',
        status='pending'
    )
    db.session.add(appt)
    db.session.commit()
    
    # Send booking notification immediately
    notification_service.send_booking_notification(appt)
    
    # Use AI routing to allocate doctor
    allocated_doctor = appointment_router.route_appointment(appt)
    
    # Send confirmation notification to patient only if allocated
    if allocated_doctor:
        notification_service.send_appointment_confirmation(appt, allocated_doctor)
    
    return jsonify({
        'ok': True,
        'appointment_id': appt.id,
        'patient_id': patient.id,
        'patient_code': patient.patient_id,
        'status': appt.status,
        'doctor': f"Dr. {allocated_doctor.first_name} {allocated_doctor.last_name}" if allocated_doctor else "To be assigned",
        'message': 'Appointment request submitted and routed to appropriate doctor. You will receive a confirmation message shortly.'
    })


@app.route('/patient-portal/medical-records')
@patient_login_required
def patient_medical_records_page():
    """Medical records page for patient portal"""
    patient = get_logged_in_patient()
    
    # Get all records
    doctor_notes = DoctorNote.query.filter_by(patient_id=patient.id).order_by(DoctorNote.created_at.desc()).all()
    treatments = TreatmentLog.query.filter_by(patient_id=patient.id).order_by(TreatmentLog.performed_at.desc()).all()
    vitals = VitalSign.query.filter_by(patient_id=patient.id).order_by(VitalSign.recorded_at.desc()).limit(50).all()
    medications = Medication.query.filter_by(patient_id=patient.id).all()
    alerts = Alert.query.filter_by(patient_id=patient.id).order_by(Alert.created_at.desc()).limit(20).all()
    
    return render_template('patient_portal_medical_records.html',
                         patient=patient,
                         doctor_notes=doctor_notes,
                         treatments=treatments,
                         vitals=vitals,
                         medications=medications,
                         alerts=alerts)


@app.errorhandler(404)
def not_found(e):
    return render_template('404.html'), 404


@app.errorhandler(403)
def forbidden(e):
    return render_template('403.html'), 403


# ===== NURSE DASHBOARD MODIFICATIONS =====

@app.route('/admin/wards', methods=['GET', 'POST'])
@staff_login_required
@admin_required
def admin_wards():
    staff = get_staff_user()
    
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'create':
            name = request.form.get('name')
            capacity = request.form.get('capacity')
            floor = request.form.get('floor')
            
            if Ward.query.filter_by(name=name).first():
                flash('Ward with this name already exists.', 'danger')
            else:
                new_ward = Ward(name=name, capacity=capacity, floor=floor)
                db.session.add(new_ward)
                db.session.commit()
                flash('Ward created successfully.', 'success')
                
        elif action == 'edit':
            ward_id = request.form.get('ward_id')
            ward = Ward.query.get_or_404(ward_id)
            ward.name = request.form.get('name')
            ward.capacity = request.form.get('capacity')
            ward.floor = request.form.get('floor')
            head_nurse_id = request.form.get('head_nurse_id')
            if head_nurse_id:
                ward.head_nurse_id = head_nurse_id
            db.session.commit()
            flash('Ward updated successfully.', 'success')
            
        return redirect(url_for('admin_wards'))

    wards = Ward.query.order_by(Ward.name).all()
    
    # Calculate occupancy for each ward
    ward_stats = []
    for w in wards:
        occupancy = Patient.query.filter_by(department=w.name, status='admitted').count()
        ward_stats.append({
            'ward': w,
            'occupancy': occupancy,
            'percent': int((occupancy / w.capacity) * 100) if w.capacity > 0 else 0
        })
        
    nurses = StaffMember.query.filter_by(role='nurse').all()
    return render_template('admin/wards.html', staff=staff, ward_stats=ward_stats, nurses=nurses)


@app.route('/admin/assign-staff', methods=['GET', 'POST'])
@staff_login_required
@admin_required
def admin_assign_staff():
    staff_user = get_staff_user()
    
    if request.method == 'POST':
        staff_id = request.form.get('staff_id')
        new_dept = request.form.get('department')
        
        target_staff = StaffMember.query.get(staff_id)
        if target_staff:
            target_staff.department = new_dept
            db.session.commit()
            flash(f'Updated {target_staff.full_name} to {new_dept}.', 'success')
        else:
            flash('Staff member not found.', 'danger')
            
        return redirect(url_for('admin_assign_staff'))

    _TEST_STAFF_IDS = ['DOC_RT', 'NRS_RT', 'NRS_ICU', 'NRS_ER']
    all_staff = StaffMember.query.filter(
        StaffMember.role != 'admin',
        ~StaffMember.staff_id.in_(_TEST_STAFF_IDS)
    ).order_by(StaffMember.role, StaffMember.last_name).all()
    wards = Ward.query.order_by(Ward.name).all()
    
    return render_template('admin/staff_assignment.html', staff=staff_user, all_staff=all_staff, wards=wards)
    """Look up patient by ID for modification (returns partial HTML)"""
    staff = get_staff_user()
    patient_id_str = request.args.get('patient_id', '').strip()
    
    # Try exact match first, then case-insensitive
    patient = Patient.query.filter_by(patient_id=patient_id_str).first()
    if not patient:
         patient = Patient.query.filter(Patient.patient_id.ilike(patient_id_str)).first()
            
    if not patient:
        return jsonify({'error': 'Patient ID not found.'}), 404
        
    # Audit View (Nurse Lookup)
    try:
        log = AuditLog(
            action='VIEW',
            table_name='patients',
            record_id=patient.id,
            field_name='all',
            old_value=None,
            new_value='lookup_for_edit',
            changed_by_id=staff.id
        )
        db.session.add(log)
        db.session.commit()
    except Exception as e:
        logging.error(f"Audit Log Error: {e}")
        
    return render_template('nurse/edit_patient_partial.html', staff=staff, patient=patient)


@app.route('/nurse/patient-lookup', methods=['GET'])
@staff_login_required
@role_required('nurse', 'admin')
def nurse_patient_lookup():
    """AJAX endpoint to lookup patient and return edit form"""
    patient_id = request.args.get('patient_id', '').strip()
    
    if not patient_id:
        return jsonify({'error': 'Patient ID required'}), 400
    
    patient = Patient.query.filter_by(patient_id=patient_id).first()
    
    if not patient:
        return jsonify({'error': f'Patient {patient_id} not found'}), 404
    
    # Get existing medications
    current_meds = Medication.query.filter_by(patient_id=patient.id, is_active=True).all()
    
    # Return HTML for edit form
    html = f'''
    <div class="alert alert-info">
        <strong>{patient.full_name}</strong> - {patient.patient_id} | Room {patient.room_number or '-'} | Status: <span class="badge bg-primary">{patient.status.upper()}</span>
    </div>
    
    <form class="row g-3" id="patientEditForm" data-patient-id="{patient.id}">
        <div class="col-md-6">
            <label for="diagnosis" class="form-label">Diagnosis</label>
            <textarea class="form-control" id="diagnosis" name="diagnosis" rows="2">{patient.diagnosis or ''}</textarea>
        </div>
        
        <div class="col-md-6">
            <label for="notes" class="form-label">Medical Notes</label>
            <textarea class="form-control" id="notes" name="notes" rows="2">{patient.notes or ''}</textarea>
        </div>
        
        <div class="col-md-3">
            <label for="room_number" class="form-label">Room Number</label>
            <input type="text" class="form-control" id="room_number" name="room_number" value="{patient.room_number or ''}">
        </div>
        
        <div class="col-md-3">
            <label for="bed_number" class="form-label">Bed Number</label>
            <input type="text" class="form-control" id="bed_number" name="bed_number" value="{patient.bed_number or ''}">
        </div>
        
        <div class="col-md-6">
            <label for="status" class="form-label">Status</label>
            <select class="form-select" id="status" name="status">
                <option value="admitted" {'selected' if patient.status == 'admitted' else ''}>Admitted</option>
                <option value="icu" {'selected' if patient.status == 'icu' else ''}>ICU</option>
                <option value="emergency" {'selected' if patient.status == 'emergency' else ''}>Emergency</option>
                <option value="discharged" {'selected' if patient.status == 'discharged' else ''}>Discharged</option>
            </select>
        </div>
        
        <hr class="col-12">
        
        <!-- Medication Management Section -->
        <div class="col-12">
            <h6 class="mb-3"><i class="bi bi-capsule me-2"></i>Medication Management</h6>
        </div>
        
        <div class="col-md-4">
            <label for="medication_name" class="form-label">Medicine Name</label>
            <input type="text" class="form-control" id="medication_name" placeholder="e.g., Aspirin, Ibuprofen">
        </div>
        
        <div class="col-md-4">
            <label for="medication_dosage" class="form-label">Dosage</label>
            <select class="form-select" id="medication_dosage">
                <option value="">-- Select Dosage --</option>
                <option value="5mg">5 mg</option>
                <option value="10mg">10 mg</option>
                <option value="25mg">25 mg</option>
                <option value="50mg">50 mg</option>
                <option value="100mg">100 mg</option>
                <option value="250mg">250 mg</option>
                <option value="500mg">500 mg</option>
                <option value="1000mg">1000 mg</option>
                <option value="250ml">250 ml</option>
                <option value="500ml">500 ml</option>
                <option value="1 unit">1 unit</option>
                <option value="2 units">2 units</option>
                <option value="5 units">5 units</option>
                <option value="10 units">10 units</option>
                <option value="1 tablet">1 tablet</option>
                <option value="2 tablets">2 tablets</option>
                <option value="0.5 tablet">0.5 tablet</option>
            </select>
        </div>
        
        <div class="col-md-4">
            <label for="medication_timing" class="form-label">Timing / Frequency</label>
            <select class="form-select" id="medication_timing">
                <option value="">-- Select Timing --</option>
                <option value="Once daily">Once daily</option>
                <option value="Twice daily">Twice daily (Morning & Evening)</option>
                <option value="Three times daily">Three times daily (Morning, Afternoon, Evening)</option>
                <option value="Four times daily">Four times daily</option>
                <option value="Every 4 hours">Every 4 hours</option>
                <option value="Every 6 hours">Every 6 hours</option>
                <option value="Every 8 hours">Every 8 hours</option>
                <option value="Every 12 hours">Every 12 hours</option>
                <option value="As needed">As needed</option>
            </select>
        </div>
        
        <div class="col-md-4">
            <label for="medication_route" class="form-label">Route</label>
            <select class="form-select" id="medication_route">
                <option value="oral" selected>Oral</option>
                <option value="intravenous">Intravenous (IV)</option>
                <option value="intramuscular">Intramuscular (IM)</option>
                <option value="topical">Topical</option>
                <option value="sublingual">Sublingual</option>
                <option value="inhalation">Inhalation</option>
            </select>
        </div>
        
        <div class="col-md-8">
            <label for="medication_notes" class="form-label">Notes</label>
            <input type="text" class="form-control" id="medication_notes" placeholder="e.g., with food, after meals">
        </div>
        
        <div class="col-12">
            <button type="button" class="btn btn-info" id="addMedicationBtn">
                <i class="bi bi-plus-circle me-2"></i>Add Medication
            </button>
        </div>
        
        <!-- Current Medications List -->
        <div class="col-12" id="currentMedsSection" style="display: {'block' if current_meds else 'none'};">
            <h6 class="mt-3 mb-3"><i class="bi bi-list-check me-2"></i>Current Medications</h6>
            <div class="table-responsive">
                <table class="table table-sm table-hover" id="currentMedicationsTable">
                    <thead class="table-light">
                        <tr>
                            <th>Medicine</th>
                            <th>Dosage</th>
                            <th>Frequency</th>
                            <th>Route</th>
                            <th>Action</th>
                        </tr>
                    </thead>
                    <tbody id="currentMedsBody">
    '''
    
    for med in current_meds:
        html += f'''
                        <tr data-med-id="{med.id}">
                            <td><strong>{med.name}</strong></td>
                            <td>{med.dosage}</td>
                            <td>{med.frequency}</td>
                            <td>{med.route or 'oral'}</td>
                            <td>
                                <button type="button" class="btn btn-sm btn-danger remove-med-btn" data-med-id="{med.id}">
                                    <i class="bi bi-trash"></i>
                                </button>
                            </td>
                        </tr>
        '''
    
    html += f'''
                    </tbody>
                </table>
            </div>
        </div>
        
        <div class="col-12">
            <button type="submit" class="btn btn-success">
                <i class="bi bi-check-circle me-2"></i>Save Changes
            </button>
            <button type="reset" class="btn btn-secondary ms-2">
                <i class="bi bi-x-circle me-2"></i>Reset
            </button>
        </div>
    </form>
    
    <script>
        // Store medications being added in session
        let newMedications = [];
        
        document.getElementById('patientEditForm').addEventListener('submit', function(e) {{
            e.preventDefault();
            const patientId = this.getAttribute('data-patient-id');
            const formData = new FormData(this);
            
            // Add new medications to form data
            formData.append('new_medications', JSON.stringify(newMedications));
            
            fetch(`/nurse/update-patient/${{patientId}}`, {{
                method: 'POST',
                body: formData
            }})
            .then(response => response.json())
            .then(data => {{
                if (data.success) {{
                    alert('Patient record and medications updated successfully!');
                    location.reload();
                }} else {{
                    alert('Error: ' + (data.error || 'Unknown error'));
                }}
            }})
            .catch(error => {{
                alert('Error updating patient: ' + error.message);
            }});
        }});
        
        // Add medication button handler
        document.getElementById('addMedicationBtn').addEventListener('click', function() {{
            const medName = document.getElementById('medication_name').value.trim();
            const medDosage = document.getElementById('medication_dosage').value;
            const medTiming = document.getElementById('medication_timing').value;
            const medRoute = document.getElementById('medication_route').value;
            const medNotes = document.getElementById('medication_notes').value.trim();
            
            if (!medName) {{
                alert('Please enter medicine name');
                return;
            }}
            if (!medDosage) {{
                alert('Please select dosage');
                return;
            }}
            if (!medTiming) {{
                alert('Please select timing/frequency');
                return;
            }}
            
            // Add to list
            newMedications.push({{
                name: medName,
                dosage: medDosage,
                frequency: medTiming,
                route: medRoute,
                notes: medNotes
            }});
            
            // Add row to table
            const tbody = document.getElementById('currentMedsBody');
            const tr = document.createElement('tr');
            tr.classList.add('table-success');
            tr.innerHTML = `
                <td><strong>${{medName}}</strong> <span class="badge bg-success ms-2">NEW</span></td>
                <td>${{medDosage}}</td>
                <td>${{medTiming}}</td>
                <td>${{medRoute}}</td>
                <td>
                    <button type="button" class="btn btn-sm btn-danger remove-new-med-btn">
                        <i class="bi bi-trash"></i>
                    </button>
                </td>
            `;
            
            // Remove button handler
            tr.querySelector('.remove-new-med-btn').addEventListener('click', function() {{
                tr.remove();
                const idx = newMedications.findIndex(m => m.name === medName && m.dosage === medDosage);
                if (idx > -1) newMedications.splice(idx, 1);
                if (newMedications.length === 0 && document.querySelectorAll('#currentMedsBody tr').length === 0) {{
                    document.getElementById('currentMedsSection').style.display = 'none';
                }}
            }});
            
            tbody.appendChild(tr);
            document.getElementById('currentMedsSection').style.display = 'block';
            
            // Clear form
            document.getElementById('medication_name').value = '';
            document.getElementById('medication_dosage').value = '';
            document.getElementById('medication_timing').value = '';
            document.getElementById('medication_route').value = 'oral';
            document.getElementById('medication_notes').value = '';
            document.getElementById('medication_name').focus();
        }});
        
        // Remove existing medications
        document.querySelectorAll('.remove-med-btn').forEach(btn => {{
            btn.addEventListener('click', function() {{
                const medId = this.getAttribute('data-med-id');
                fetch(`/api/medication/${{medId}}/deactivate`, {{
                    method: 'POST',
                    headers: {{'X-Requested-With': 'XMLHttpRequest'}}
                }})
                .then(response => response.json())
                .then(data => {{
                    if (data.success) {{
                        this.closest('tr').remove();
                        if (document.querySelectorAll('#currentMedsBody tr').length === 0 && newMedications.length === 0) {{
                            document.getElementById('currentMedsSection').style.display = 'none';
                        }}
                    }} else {{
                        alert('Error removing medication');
                    }}
                }});
    </script>
    '''
    
    return html


@app.route('/nurse/update-patient/<int:patient_id>', methods=['POST'])
@staff_login_required
@role_required('nurse', 'admin')
def update_patient_record(patient_id):
    """Update patient record with audit trail and medications"""
    staff = get_staff_user()
    patient = Patient.query.get_or_404(patient_id)
    
    # Fields allowed to edit
    editable_fields = ['diagnosis', 'notes', 'room_number', 'bed_number', 'status']
    
    changes_made = False
    
    try:
        # Update existing fields
        for field in editable_fields:
            new_value = request.form.get(field)
            if new_value is not None:
                new_value = new_value.strip()
                old_value = getattr(patient, field) or ''
                
                # Check if changed (handling None vs empty string)
                if str(old_value) != str(new_value):
                    # Record change
                    setattr(patient, field, new_value)
                    
                    # Create Audit Log
                    audit = AuditLog(
                        action='UPDATE',
                        table_name='patients',
                        record_id=patient.id,
                        field_name=field,
                        old_value=str(old_value),
                        new_value=new_value,
                        changed_by_id=staff.id
                    )
                    db.session.add(audit)
                    changes_made = True
        
        # Handle new medications
        new_meds_json = request.form.get('new_medications', '[]')
        try:
            new_meds = json.loads(new_meds_json)
            for med_data in new_meds:
                if med_data.get('name') and med_data.get('dosage') and med_data.get('frequency'):
                    # Calculate next_due based on frequency
                    freq_hours = {
                        'Once daily': 24,
                        'Twice daily': 12,
                        'Three times daily': 8,
                        'Four times daily': 6,
                        'Every 4 hours': 4,
                        'Every 6 hours': 6,
                        'Every 8 hours': 8,
                        'Every 12 hours': 12,
                        'As needed': 0
                    }.get(med_data.get('frequency'), 24)
                    
                    from datetime import timedelta
                    next_due = datetime.now() + timedelta(hours=freq_hours) if freq_hours > 0 else None
                    
                    medication = Medication(
                        patient_id=patient_id,
                        name=med_data.get('name'),
                        dosage=med_data.get('dosage'),
                        frequency=med_data.get('frequency'),
                        route=med_data.get('route', 'oral'),
                        start_date=datetime.now(),
                        next_due=next_due,
                        notes=med_data.get('notes', ''),
                        is_active=True
                    )
                    db.session.add(medication)
                    changes_made = True
        except (json.JSONDecodeError, AttributeError):
            logging.warning(f"Invalid medications JSON for patient {patient_id}")
        
        if changes_made:
            patient.updated_at = datetime.now()
            db.session.commit()
            
            # Create notification for assigned nurse if exists
            if patient.assigned_nurse_id and patient.assigned_nurse_id != staff.id:
                from models import Notification
                notification = Notification(
                    recipient_id=patient.assigned_nurse_id,
                    notification_type='patient_update',
                    title=f'Patient Record Updated: {patient.full_name}',
                    message=f'{staff.full_name} updated patient record for {patient.full_name} (Room {patient.room_number}). New status: {patient.status.upper()}',
                    patient_id=patient.id,
                    is_read=False,
                    is_acknowledged=False
                )
                db.session.add(notification)
                db.session.commit()
            
            return jsonify({'success': True})
        else:
            return jsonify({'success': True, 'message': 'No changes detected'})
            
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error updating patient {patient_id}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/medication/<int:medication_id>/deactivate', methods=['POST'])
@staff_login_required
def deactivate_medication(medication_id):
    """Deactivate a medication"""
    try:
        medication = Medication.query.get_or_404(medication_id)
        medication.is_active = False
        db.session.commit()
        return jsonify({'success': True}), 200
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error deactivating medication {medication_id}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/nurse/notifications')
@staff_login_required
@role_required('nurse', 'admin')
def get_nurse_notifications():
    """Fetch unacknowledged notifications for the current nurse"""
    staff = get_staff_user()
    
    # Get unacknowledged notifications for this nurse
    from models import Notification
    notifications = Notification.query.filter(
        Notification.recipient_id == staff.id,
        Notification.is_acknowledged == False
    ).order_by(Notification.created_at.desc()).all()
    
    notification_list = []
    for notif in notifications:
        notification_list.append({
            'id': notif.id,
            'type': notif.notification_type,
            'title': notif.title,
            'message': notif.message,
            'patient_id': notif.patient_id,
            'patient_name': notif.patient.full_name if notif.patient else 'N/A',
            'is_read': notif.is_read,
            'created_at': notif.created_at.isoformat()
        })
    
    return jsonify({
        'success': True,
        'count': len(notification_list),
        'notifications': notification_list
    }), 200


@app.route('/api/notification/<int:notification_id>/acknowledge', methods=['POST'])
@staff_login_required
def acknowledge_notification(notification_id):
    """Mark notification as acknowledged"""
    try:
        from models import Notification
        notification = Notification.query.get_or_404(notification_id)
        notification.is_acknowledged = True
        notification.is_read = True
        db.session.commit()
        return jsonify({'success': True}), 200
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error acknowledging notification {notification_id}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/notification/<int:notification_id>/read', methods=['POST'])
@staff_login_required
def mark_notification_read(notification_id):
    """Mark notification as read"""
    try:
        from models import Notification
        notification = Notification.query.get_or_404(notification_id)
        notification.is_read = True
        db.session.commit()
        return jsonify({'success': True}), 200
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error marking notification read {notification_id}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/book-appointment-public', methods=['POST'])
def book_appointment_public():
    """Handle public appointment requests from landing page"""
    try:
        # 1. Extract form data
        first_name = request.form.get('first_name', '').strip()
        last_name = request.form.get('last_name', '').strip()
        email = request.form.get('email', '').strip()
        phone_raw = request.form.get('phone', '').strip()
        phone = format_to_e164(phone_raw)
        dob_str = request.form.get('date_of_birth')
        gender = request.form.get('gender')
        pref_date_str = request.form.get('preferred_date')
        pref_time = request.form.get('preferred_time')
        problem = request.form.get('problem_description', '')
        department = request.form.get('department', 'General Medicine')

        if not all([first_name, last_name, dob_str, gender, pref_date_str, pref_time]):
            flash('Please fill in all required fields.', 'danger')
            return redirect(url_for('index'))

        # 2. Find or Create Patient
        # Simple matching logic: email or phone
        patient = None
        if email:
            patient = Patient.query.filter_by(email=email).first()
        if patient:
            if phone and patient.phone != phone:
                patient.phone = phone
                db.session.commit()
                logging.info(f"Updated phone number for patient {patient.id} to {phone}")
        else:
            # Robust Patient ID generation
            import re
            all_patients = Patient.query.filter(Patient.patient_id.like('PAT%')).all()
            max_num = 0
            for p in all_patients:
                match = re.search(r'PAT(\d+)', p.patient_id)
                if match:
                    num = int(match.group(1))
                    if num > max_num:
                        max_num = num
            
            next_num = max_num + 1
            new_patient_id = f"PAT{str(next_num).zfill(4)}"
            
            # Create new patient
            try:
                dob = datetime.strptime(dob_str, '%Y-%m-%d').date()
            except ValueError:
                flash('Invalid date format for Date of Birth.', 'danger')
                return redirect(url_for('index'))

            patient = Patient(
                patient_id=new_patient_id,
                first_name=first_name,
                last_name=last_name,
                email=email,
                phone=phone,
                date_of_birth=dob,
                gender=gender,
                status='outpatient', # New status for these patients
                diagnosis=problem # Initial diagnosis/complaint
            )
            db.session.add(patient)
            db.session.commit()
            logging.info(f"Created new patient {new_patient_id} for appointment request.")

        # 3. Create Appointment Request
        try:
            pref_date = datetime.strptime(pref_date_str, '%Y-%m-%d')
            # Combine if strictly needed, but model has separate fields
        except ValueError:
            flash('Invalid date format for Appointment Date.', 'danger')
            return redirect(url_for('index'))

        appt = AppointmentRequest(
            patient_id=patient.id,
            preferred_date=pref_date,
            preferred_time=pref_time,
            notes=problem,
            status='pending',
            source='landing_page',
            appointment_type='consultation',
            department=department
        )
        db.session.add(appt)
        db.session.commit()

        # Send booking notification
        notification_service.send_booking_notification(appt)

        # Use AI routing to allocate doctor
        allocated_doctor = appointment_router.route_appointment(appt)
        if allocated_doctor:
            logging.info(f"AI allocated appointment {appt.id} to Dr. {allocated_doctor.first_name} {allocated_doctor.last_name}")
            # Send confirmation notification to patient
            notification_service.send_appointment_confirmation(appt, allocated_doctor)
            flash('Your appointment request has been submitted successfully! We will contact you shortly.', 'success')
        else:
            logging.warning(f"Could not allocate doctor for appointment {appt.id}")
            flash('Your appointment request has been submitted. No doctor available at this time. We will contact you shortly to confirm.', 'warning')

        return redirect(url_for('index'))

    except Exception as e:
        db.session.rollback()
        logging.error(f"Error in book_appointment_public: {e}")
        flash('An error occurred while processing your request. Please try again.', 'danger')
        return redirect(url_for('index'))


@app.route('/api/medicine-details/<string:med_name>')
def get_medicine_details(med_name):
    """Fetch general information about a medicine by name"""
    # Try exact match first, then case-insensitive
    med = MedicineDetail.query.filter_by(name=med_name).first()
    if not med:
        med = MedicineDetail.query.filter(MedicineDetail.name.ilike(f"%{med_name}%")).first()
    
    if med:
        return jsonify({
            'success': True,
            'name': med.name,
            'usage': med.usage,
            'dosage': med.dosage,
            'precautions': med.precautions
        })
    else:
        return jsonify({
            'success': False,
            'error': 'Medicine details not found'
        }), 404



@app.route('/api/arduino/vitals', methods=['POST'])
def update_arduino_vitals():
    """Ingest a vitals reading from the Arduino bridge and act on it.

    The same routing path as the background monitor is used, so a hardware
    reading produces exactly one consolidated alert, delivered to the patient's
    assigned doctor *and* assigned nurse.  An Arduino patient with no care team
    is routed one automatically, so the hardware always has an owner.
    """
    from routing import _publish_alert, create_patient_alert, evaluate_vitals

    data = request.get_json(silent=True) or {}
    bpm = data.get('bpm')
    spo2 = data.get('spo2')
    temp = data.get('temp')
    if bpm is None and spo2 is None and temp is None:
        return jsonify({'error': 'No vital readings supplied'}), 400

    patient = _resolve_arduino_patient(data)
    if not patient:
        log.error('Arduino reading received but no target patient could be resolved')
        return jsonify({'error': 'Target patient not found'}), 404

    # Guarantee the hardware patient has a doctor and a nurse to alert.
    if not patient.assigned_doctor_id or not patient.assigned_nurse_id:
        from routing import router
        router.route_patient(patient, reason='Arduino device attached')

    vital = VitalSign(
        patient_id=patient.id,
        heart_rate=bpm,
        oxygen_saturation=spo2,
        temperature=temp,
        blood_pressure_systolic=None,
        blood_pressure_diastolic=None,
        respiratory_rate=data.get('resp'),
        recorded_at=datetime.now(),
        status='normal',
    )
    db.session.add(vital)
    db.session.commit()

    severity, breaches = evaluate_vitals(vital, patient)
    alert = None
    if severity and breaches:
        vital.status = severity
        db.session.commit()

        headline = breaches[0].split(' (')[0]
        title = ('CRITICAL: ' if severity == 'critical' else 'Warning: ') + \
                f"{patient.full_name} - {headline}"
        message = f"{patient.location_label}. {'; '.join(breaches)}."

        # One alert per reading, routed to the assigned doctor and nurse.
        alert, recipients = create_patient_alert(
            patient, vital, severity, title, message)
        db.session.commit()
        _publish_alert(alert, patient, recipients)
        send_to_n8n_webhook(alert)

        if severity == 'critical':
            # A caretaking contact is notified over SMS for critical events.
            send_vitals_alert_sms(
                patient.full_name, f"EMERGENCY: {patient.full_name}",
                'Doc/Nurse/Caretaker Alert:\n' + message)

        # Push the live vitals reading to both assigned dashboards.
        socketio.emit('vital_update', {
            'patient_id': patient.id,
            'heart_rate': vital.heart_rate,
            'bp_systolic': None,
            'bp_diastolic': None,
            'oxygen': vital.oxygen_saturation,
            'temperature': vital.temperature,
            'status': vital.status,
            'timestamp': vital.recorded_at.strftime('%H:%M:%S'),
        }, to='staff_{0}'.format(patient.assigned_doctor_id) if patient.assigned_doctor_id else None)
        if patient.assigned_nurse_id:
            socketio.emit('vital_update', {
                'patient_id': patient.id,
                'heart_rate': vital.heart_rate,
                'bp_systolic': None,
                'bp_diastolic': None,
                'oxygen': vital.oxygen_saturation,
                'temperature': vital.temperature,
                'status': vital.status,
                'timestamp': vital.recorded_at.strftime('%H:%M:%S'),
            }, to=f'staff_{patient.assigned_nurse_id}')

        log.info('Arduino reading for %s: %s (%d recipient(s))',
                 patient.full_name, severity, len(recipients))
    else:
        vital.status = 'normal'
        db.session.commit()

    return jsonify({
        'ok': True,
        'patient_id': patient.id,
        'patient_name': patient.full_name,
        'status': vital.status,
        'alert_id': alert.id if alert else None,
        'recipients': [s.staff_id for s in
                         (alert_recipients(patient) if alert else [])],
        'message': f'Vitals for {patient.full_name} updated',
    })


def _resolve_arduino_patient(data):
    """Find the patient a hardware reading belongs to.

    Resolution order: explicit ``patient_id`` in the payload (integer id or string code),
    then the ``ARDUINO_PATIENT_ID`` environment variable, then a patient flagged as
    having a device attached, then first active patient.
    """
    candidate = data.get('patient_id') or os.environ.get('ARDUINO_PATIENT_ID')
    if candidate is not None:
        if isinstance(candidate, int):
            patient = Patient.query.get(candidate)
            if patient:
                return patient
        patient_code = str(candidate).strip().upper()
        patient = Patient.query.filter_by(patient_id=patient_code).first()
        if patient:
            return patient

    # Match designated hardware patient Pavanth Kumar Chopparapu / PAT0128
    named_patient = Patient.query.filter_by(patient_id='PAT0128').first()
    if not named_patient:
        named_patient = Patient.query.filter(
            db.func.lower(Patient.first_name) == 'pavanth',
            db.func.lower(Patient.last_name).like('%chopparapu%')
        ).first()
    if named_patient:
        return named_patient

    # Fall back to patient with arduino in notes or first admitted patient
    flagged = Patient.query.filter(
        Patient.notes.ilike('%arduino%'),
        Patient.status.in_(['admitted', 'icu', 'emergency']),
    ).first()
    if flagged:
        return flagged
    return Patient.query.filter(Patient.status.in_(['admitted', 'icu', 'emergency'])).first()
