import random
import logging
import threading
from datetime import datetime

logging.basicConfig(level=logging.DEBUG)

new_alerts = []
alert_paths = {}
_demo_cycle_lock = threading.Lock()
_last_demo_cycle = None


def generate_presentation_alerts(force=False):
    """Create an immediate demo cycle for presentation dashboards."""
    global _last_demo_cycle
    now = datetime.now()
    if not force and _last_demo_cycle and (now - _last_demo_cycle).total_seconds() < 20:
        return {'vital_alerts': 0, 'medication_alerts': 0}
    if not _demo_cycle_lock.acquire(blocking=False):
        return {'vital_alerts': 0, 'medication_alerts': 0}
    try:
        _last_demo_cycle = now
        update_patient_vitals()
        return {'vital_alerts': 1, 'medication_alerts': 0}
    except Exception as exc:
        logging.error(f"Error in generate_presentation_alerts: {exc}")
        return {'vital_alerts': 0, 'medication_alerts': 0}
    finally:
        _demo_cycle_lock.release()


def update_patient_vitals():
    global new_alerts
    # Import inside function to avoid circular imports
    from app import app, db
    from models import Patient, VitalSign, Alert, StaffMember
    from synthetic_data import generate_vital_sign, check_vital_thresholds, create_alert
    from routing import (
        _publish_alert, alert_throttle,
        distribute_alerts_to_staff, reroute_patient,
    )
    
    with app.app_context():
        try:
            # Specifically simulate for Pavanth Kumar Chopparapu (PAT0128)
            patient_pavan = Patient.query.filter_by(patient_id='PAT0128').first()
            
            patients = Patient.query.filter(Patient.status.in_(['admitted', 'icu', 'emergency'])).all()
            
            if not patients:
                logging.info("No active patients to update vitals for")
                return
            
            # Add Pavanth to the list if he exists and is not already there
            if patient_pavan and patient_pavan not in patients:
                patients.append(patient_pavan)

            patients_to_update = random.sample(patients, min(len(patients), max(3, len(patients) // 2)))
            
            # Dictionary to track consecutive critical alerts per patient session
            if not hasattr(update_patient_vitals, "consecutive_critical_count"):
                update_patient_vitals.consecutive_critical_count = {}

            for patient in patients_to_update:
                status_bias = None
                if patient.status == 'icu':
                    status_bias = random.choices(['critical', 'warning', None], weights=[20, 30, 50])[0]
                elif patient.status == 'emergency':
                    status_bias = random.choices(['critical', 'warning', None], weights=[30, 40, 30])[0]
                else:
                    status_bias = random.choices(['critical', 'warning', None], weights=[5, 10, 85])[0]
                
                vital = generate_vital_sign(patient, status_bias)
                db.session.add(vital)
                db.session.commit()
                # Emit real-time update - Targeted to assigned staff only
                try:
                    from app import socketio
                    vital_payload = {
                        'patient_id': patient.id,
                        'heart_rate': vital.heart_rate,
                        'bp_systolic': vital.blood_pressure_systolic,
                        'bp_diastolic': vital.blood_pressure_diastolic,
                        'oxygen': vital.oxygen_saturation,
                        'temperature': vital.temperature,
                        'status': vital.status,
                        'timestamp': vital.recorded_at.strftime('%H:%M:%S')
                    }
                    if patient.assigned_doctor_id:
                        socketio.emit('vital_update', vital_payload, to=f"staff_{patient.assigned_doctor_id}")
                    if patient.assigned_nurse_id:
                        socketio.emit('vital_update', vital_payload, to=f"staff_{patient.assigned_nurse_id}")
                except Exception as e:
                    logging.error(f"Socket emit error: {e}")

                alert_data = check_vital_thresholds(vital)
                
                # If no critical alerts, reset the counter
                has_critical = any(a['severity'] == 'critical' for a in alert_data)
                if not has_critical:
                    update_patient_vitals.consecutive_critical_count[patient.id] = 0
                else:
                    update_patient_vitals.consecutive_critical_count[patient.id] = update_patient_vitals.consecutive_critical_count.get(patient.id, 0) + 1
                    logging.info(f"Patient {patient.id} has {update_patient_vitals.consecutive_critical_count[patient.id]} consecutive critical alerts")

                triggering_alert_ids = []
                for alert in alert_data:
                    alert_obj = Alert(
                        patient_id=patient.id,
                        vital_sign_id=vital.id,
                        alert_type=alert['type'],
                        severity=alert['severity'],
                        title=alert['title'],
                        message=alert['message']
                    )
                    db.session.add(alert_obj)
                    db.session.commit()
                    triggering_alert_ids.append(alert_obj.id)
                    
                    # Throttle alerts: only one per patient per dashboard session
                    if not alert_throttle.can_send(patient.id):
                        logging.debug(f"Alert for patient {patient.id} suppressed by throttling.")
                        continue

                    recipients = distribute_alerts_to_staff(
                        patient.id,
                        alert['severity'],
                        alert_obj.id
                    )
                    # Record that an alert was sent for this patient
                    alert_throttle.record_sent(patient.id)
                    
                    _publish_alert(alert_obj, patient, recipients)

                    routing_path = []
                    if recipients:
                        routing_path = [staff.staff_id for staff in recipients]
                    
                    for staff in recipients:
                        new_alerts.append({
                            'staff_id': staff.id,
                            'staff_name': staff.full_name,
                            'patient_id': patient.id,
                            'patient_name': patient.full_name,
                            'room': patient.room_number,
                            'bed': patient.bed_number,
                            'type': alert['type'],
                            'severity': alert['severity'],
                            'title': alert['title'],
                            'message': alert['message'],
                            'routing_path': routing_path,
                            'timestamp': datetime.now().isoformat()
                        })
                
                # Check for rerouting trigger
                if update_patient_vitals.consecutive_critical_count.get(patient.id, 0) >= 2:
                    logging.warning(f"Patient {patient.id} reached 2 consecutive critical alerts. Rerouting...")
                    reroute_patient(patient, triggering_alert_ids)
                    update_patient_vitals.consecutive_critical_count[patient.id] = 0

            logging.info(f"Updated vitals for {len(patients_to_update)} patients")
            
        except Exception as e:
            logging.error(f"Error updating vitals: {e}")
            db.session.rollback()


def reroute_patient(patient, alert_ids):
    """Automatically reroute a patient to another doctor and nurse"""
    from app import db
    from models import StaffMember, AlertRerouting
    import json

    old_doc_id = patient.assigned_doctor_id
    old_nurse_id = patient.assigned_nurse_id

    # Find available on-duty doctors excluding the current one
    new_doctor = StaffMember.query.filter(
        StaffMember.role == 'doctor',
        StaffMember.is_on_duty == True,
        StaffMember.is_active == True,
        StaffMember.id != old_doc_id
    ).first()

    # Find available on-duty nurses excluding the current one
    new_nurse = StaffMember.query.filter(
        StaffMember.role == 'nurse',
        StaffMember.is_on_duty == True,
        StaffMember.is_active == True,
        StaffMember.id != old_nurse_id
    ).first()

    if new_doctor or new_nurse:
        if new_doctor:
            patient.assigned_doctor_id = new_doctor.id
        if new_nurse:
            patient.assigned_nurse_id = new_nurse.id
        
        db.session.commit()

        # Log the rerouting
        rerouting_log = AlertRerouting(
            patient_id=patient.id,
            old_doctor_id=old_doc_id,
            old_nurse_id=old_nurse_id,
            new_doctor_id=new_doctor.id if new_doctor else None,
            new_nurse_id=new_nurse.id if new_nurse else None,
            triggering_alert_ids=json.dumps(alert_ids)
        )
        db.session.add(rerouting_log)
        db.session.commit()
        
        logging.info(f"Successfully rerouted patient {patient.id} to new staff.")
        
        # Notify the system/admin (could add socket emit here)
        try:
            from app import socketio
            socketio.emit('patient_rerouted', {
                'patient_id': patient.id,
                'patient_name': patient.full_name,
                'new_doctor': new_doctor.full_name if new_doctor else 'No change',
                'new_nurse': new_nurse.full_name if new_nurse else 'No change'
            })
        except Exception as e:
            logging.error(f"Socket emit error for rerouting: {e}")
    else:
        logging.error(f"No alternative on-duty staff found for patient {patient.id} rerouting.")


def get_and_clear_new_alerts():
    global new_alerts
    alerts = new_alerts.copy()
    new_alerts = []
    return alerts


def get_live_patient_vitals():
    from app import app
    from models import Patient
    with app.app_context():
        patients = Patient.query.filter(Patient.status.in_(['admitted', 'icu', 'emergency'])).all()
        
        vitals_data = []
        for patient in patients:
            latest_vital = patient.latest_vitals
            if latest_vital:
                vitals_data.append({
                    'patient_id': patient.id,
                    'patient_name': patient.full_name,
                    'room': patient.room_number,
                    'bed': patient.bed_number,
                    'status': patient.status,
                    'vital_status': latest_vital.status,
                    'heart_rate': latest_vital.heart_rate,
                    'bp_systolic': latest_vital.blood_pressure_systolic,
                    'bp_diastolic': latest_vital.blood_pressure_diastolic,
                    'oxygen': latest_vital.oxygen_saturation,
                    'temperature': latest_vital.temperature,
                    'respiratory_rate': latest_vital.respiratory_rate,
                    'recorded_at': latest_vital.recorded_at.isoformat()
                })
        
        return vitals_data

def start_simulation(socketio_instance):
    """Start the background simulation task"""
    import time
    
    def simulation_task():
        while True:
            logging.info("Running simulation cycle...")
            try:
                update_patient_vitals()
            except Exception as e:
                logging.error(f"Error in simulation cycle: {e}")
            time.sleep(5)

    socketio_instance.start_background_task(simulation_task)
