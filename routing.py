"""CareSync AI - patient routing, alert distribution and re-routing engine.

This module is the single authority for *who is looking after which patient*.
It replaces the previous split between ``alert_router.py`` and
``vital_simulator.py``.

Core rules enforced here
-----------------------
1. **Clear assignment** - every active patient always has exactly one assigned
   doctor and one assigned nurse.  ``route_patient`` picks both explicitly.
2. **Fair workload** - every active doctor holds at least
   ``MIN_PATIENTS_PER_DOCTOR`` (2) patients.  ``rebalance_workload`` re-balances
   the whole ward, and ``_score_doctor`` prefers under-loaded doctors so new
   assignments naturally spread out.
3. **Alerts reach both** - a patient alert is delivered to the assigned doctor
   *and* the assigned nurse, never to a scatter-broadcast of the whole shift.
4. **Per-role missed-alert re-routing** - the doctor and the nurse are tracked
   with *separate* consecutive-miss counters.  Two missed alerts in a row
   re-routes only the role that missed them.
5. **Both sides informed** - when a patient is re-routed, the outgoing and the
   incoming staff member both receive a notification, so the doctor who missed
   the alert still learns the patient moved.
6. **Low volume, one-by-one, dashboard-gated** - a patient's vital excursion
   produces a *single* consolidated alert (not one per abnormal metric), alerts
   are handed to the UI in strict sequence, and nothing is generated at all
   unless a staff dashboard is currently open.
"""

import json
import logging
import os
import random
import threading
import time
from datetime import datetime, timedelta

# ---------- Alert throttling helper ----------
class AlertThrottle:
    """Tracks per‑patient alert throttling and missed‑alert streaks.

    - `last_sent` records patients that have already received an alert in the current dashboard session.
    - `consecutive_missed` counts how many alerts a patient has missed in a row.
    """
    def __init__(self):
        self.last_sent = {}
        self.consecutive_missed = {}

    def can_send(self, patient_id):
        return patient_id not in self.last_sent

    def record_sent(self, patient_id):
        self.last_sent[patient_id] = True

    def reset(self, patient_id):
        self.last_sent.pop(patient_id, None)
        self.consecutive_missed.pop(patient_id, None)

    def increment_missed(self, patient_id):
        self.consecutive_missed[patient_id] = self.consecutive_missed.get(patient_id, 0) + 1
        return self.consecutive_missed[patient_id]

    def reset_missed(self, patient_id):
        self.consecutive_missed[patient_id] = 0

# Global throttler instance used throughout the module
alert_throttle = AlertThrottle()
from alert_router import alert_router as _alert_router

def distribute_alerts_to_staff(patient_id, alert_severity, alert_id):
    """Delegate alert distribution to the existing AlertRouter.
    Returns a list of StaffMember recipients.
    """
    return _alert_router.distribute_alert(patient_id, alert_severity, alert_id)


from models import (
    Alert,
    AlertRerouting,
    Notification,
    Patient,
    StaffMember,
    db,
)

log = logging.getLogger(__name__)


def _env_int(name, default):
    """Read a positive integer from the environment, falling back safely."""
    try:
        value = int(os.environ.get(name, default))
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


# --- Tunables (all overridable through .env) --------------------------------
MIN_PATIENTS_PER_DOCTOR = _env_int('MIN_PATIENTS_PER_DOCTOR', 2)
MAX_CONSECUTIVE_MISSES = _env_int('MAX_CONSECUTIVE_MISSES', 2)
ACK_WINDOW_SECONDS = _env_int('ACK_WINDOW_SECONDS', 45)
MONITOR_INTERVAL_SECONDS = _env_int('MONITOR_INTERVAL_SECONDS', 15)
VITALS_SAMPLE_FRACTION = float(os.environ.get('VITALS_SAMPLE_FRACTION', '0.35'))
ALERT_COOLDOWN_SECONDS = _env_int('ALERT_COOLDOWN_SECONDS', 60)

# Roles that can be re-routed independently.
ROLE_DOCTOR = 'doctor'
ROLE_NURSE = 'nurse'

# Diagnosis keywords -> department, used to pick sensible default doctors.
_DIAGNOSIS_TO_SPECIALTY = (
    (('cardio', 'myocard', 'heart', 'infarction', 'hypertension', 'angina'),
     'Cardiology'),
    (('neuro', 'stroke', 'seizure', 'brain'), 'Neurology'),
    (('pneum', 'copd', 'pulmonary', 'asthma', 'sepsis', 'infection'),
     'Emergency Medicine'),
    (('fracture', 'bone', 'orthop', 'joint'), 'Orthopedics'),
    (('cancer', 'tumor', 'malignan', 'chemo'), 'Oncology'),
    (('surg', 'post-op', 'appendic'), 'Surgery'),
    (('diabet', 'glucose', 'insulin'), 'General Medicine'),
)


def specialty_for_patient(patient):
    """Best-guess department for a patient, derived from the diagnosis text."""
    diagnosis = (patient.diagnosis or '').lower()
    for keywords, specialty in _DIAGNOSIS_TO_SPECIALTY:
        if any(keyword in diagnosis for keyword in keywords):
            return specialty
    return patient.department or 'General Medicine'


def patient_has_doctor(patient, staff):
    return bool(patient and patient.assigned_doctor_id == staff.id
                and staff.role == ROLE_DOCTOR)


def patient_has_nurse(patient, staff):
    return bool(patient and patient.assigned_nurse_id == staff.id
                and staff.role == ROLE_NURSE)



# ===========================================================================
# Assignment
# ===========================================================================
class PatientRouter:
    """Assigns and re-balances the doctor/nurse pairs that cover each patient."""

    # -- queries ----------------------------------------------------------
    @staticmethod
    def active_patients():
        """Patients that still need live monitoring, oldest admission first."""
        return Patient.query.filter(
            Patient.status.in_(('admitted', 'icu', 'emergency'))
        ).order_by(Patient.admission_date.asc()).all()

    @staticmethod
    def active_staff(role):
        """Active, on-duty staff available to take new patients."""
        return StaffMember.query.filter(
            StaffMember.role == role,
            StaffMember.is_active.is_(True),
            StaffMember.is_on_duty.is_(True),
        ).all()

    @staticmethod
    def load_of(staff):
        """How many active patients this staff member currently carries."""
        column = (Patient.assigned_doctor_id if staff.role == ROLE_DOCTOR
                  else Patient.assigned_nurse_id)
        return Patient.query.filter(
            column == staff.id,
            Patient.status.in_(('admitted', 'icu', 'emergency')),
        ).count()

    # -- scoring ----------------------------------------------------------
    def _score_doctor(self, doctor, patient):
        """Rank doctors: right speciality first, then lowest workload.

        Doctors below the ``MIN_PATIENTS_PER_DOCTOR`` floor get a large bonus so
        that new and re-routed patients top up under-loaded doctors instead of
        piling onto whoever is already busiest.
        """
        score = 0.0
        wanted = specialty_for_patient(patient)

        if doctor.specialization and doctor.specialization.lower() == wanted.lower():
            score += 100
        elif doctor.department and doctor.department.lower() == wanted.lower():
            score += 80
        elif doctor.specialization and wanted.lower() in doctor.specialization.lower():
            score += 60
        elif doctor.specialization == 'General Medicine':
            score += 25  # sensible catch-all for unclassified cases

        load = self.load_of(doctor)
        if load < MIN_PATIENTS_PER_DOCTOR:
            score += 50          # strong preference: fill the floor first
        score -= load * 5         # otherwise prefer the least busy doctor

        if doctor.is_on_duty:
            score += 10
        return score

    def _score_nurse(self, nurse, patient):
        """Rank nurses: same ward/department first, then lowest workload."""
        score = 0.0
        if patient.department and nurse.department == patient.department:
            score += 60
        if nurse.is_on_duty:
            score += 20
        score -= self.load_of(nurse) * 5
        return score

    def _pick(self, role, patient, exclude_ids=()):
        """Return the best available staff member for ``role``, or None."""
        candidates = [s for s in self.active_staff(role) if s.id not in exclude_ids]
        if not candidates:
            return None
        scorer = (self._score_doctor if role == ROLE_DOCTOR
                  else self._score_nurse)
        # Ties break deterministically so repeated runs are reproducible.
        return max(candidates, key=lambda s: (scorer(s, patient), -s.id))


    # -- public API -------------------------------------------------------
    def route_patient(self, patient, doctor=None, nurse=None,
                      notify=True, reason='initial routing'):
        """Assign a doctor *and* a nurse to ``patient``.

        Either argument may be supplied to pin one role; the other is chosen
        automatically.  Returns ``(doctor, nurse)`` - either may be ``None``
        when no eligible staff exists.
        """
        if doctor is None:
            doctor = self._pick(ROLE_DOCTOR, patient)
        if nurse is None:
            nurse = self._pick(ROLE_NURSE, patient)

        previous_doctor = patient.assigned_doctor
        previous_nurse = patient.assigned_nurse
        patient.assigned_doctor_id = doctor.id if doctor else None
        patient.assigned_nurse_id = nurse.id if nurse else None
        patient.routed_at = datetime.now()
        # A fresh pair starts with a clean slate.
        patient.doctor_miss_streak = 0
        patient.nurse_miss_streak = 0
        db.session.flush()

        if notify and (previous_doctor is not doctor or previous_nurse is not nurse):
            send_assignment_notifications(
                patient, previous_doctor, doctor, previous_nurse, nurse, reason)

        log.info("Routed %s (%s) -> Dr. %s / %s",
                 patient.full_name, patient.patient_id,
                 doctor.full_name if doctor else 'NONE',
                 nurse.full_name if nurse else 'NONE')
        return doctor, nurse

    def route_unassigned_patients(self):
        """Give every active patient that lacks a doctor or nurse a partner."""
        routed = 0
        for patient in self.active_patients():
            if not patient.assigned_doctor_id or not patient.assigned_nurse_id:
                self.route_patient(patient, reason='filling missing assignment')
                routed += 1
        if routed:
            db.session.commit()
        return routed

    def _best_patient_to_move(self, target, donor):
        """Pick the donor's patient that best fits ``target``'s speciality."""
        patients = Patient.query.filter(
            Patient.assigned_doctor_id == donor.id,
            Patient.status.in_(('admitted', 'icu', 'emergency')),
        ).all()
        if not patients:
            return None

        wanted = (target.specialization or '').lower()
        if wanted:
            for patient in patients:
                if wanted in (patient.diagnosis or '').lower():
                    return patient
        # Otherwise move the least acute patient - least disruptive.
        patients.sort(key=lambda p: (p.status != 'admitted', p.id))
        return patients[0]

    def rebalance_workload(self, minimum=None, commit=True):
        """Guarantee every active doctor holds at least ``minimum`` patients.

        Doctors below the floor borrow a patient from the most heavily loaded
        doctor, preferring a patient whose diagnosis matches the borrower's
        speciality so the outcome stays clinically sensible.
        """
        minimum = minimum or MIN_PATIENTS_PER_DOCTOR
        moved = 0

        for _ in range(200):          # generous bound; normally exits at once
            doctors = StaffMember.query.filter(
                StaffMember.role == ROLE_DOCTOR,
                StaffMember.is_active.is_(True),
            ).all()
            if not doctors:
                break

            loads = {d.id: self.load_of(d) for d in doctors}
            under = [d for d in doctors if loads[d.id] < minimum]
            if not under:
                break

            # Serve the emptiest doctor first so one pass evens things out.
            target = min(under, key=lambda d: (loads[d.id], d.id))
            # Donors must remain at or above the floor after giving one away.
            donors = [d for d in doctors
                      if loads[d.id] > minimum and d.id != target.id]
            if not donors:
                break

            donor = max(donors, key=lambda d: (loads[d.id], -d.id))
            patient = self._best_patient_to_move(target, donor)
            if patient is None:
                break

            previous_doctor = patient.assigned_doctor
            previous_nurse = patient.assigned_nurse
            patient.assigned_doctor_id = target.id
            patient.routed_at = datetime.now()

    def send_assignment_notifications(self, patient, old_doctor, new_doctor,
                                      old_nurse, new_nurse, reason):
        return send_assignment_notifications(
            patient, old_doctor, new_doctor, old_nurse, new_nurse, reason)


# ===========================================================================
# Notifications
# ===========================================================================
def _socketio():
    """Return the SocketIO server, or None when the app is not running."""
    try:
        from app import socketio
        return socketio
    except Exception:                                  # pragma: no cover
        return None


def notify_staff(staff, title, message, patient=None,
                 notification_type='assignment'):
    """Persist a notification for one staff member and push it over the socket."""
    if not staff:
        return None
    notification = Notification(
        recipient_id=staff.id,
        notification_type=notification_type,
        title=title,
        message=message,
        patient_id=patient.id if patient else None,
        is_read=False,
        is_acknowledged=False,
    )
    db.session.add(notification)
    db.session.flush()

    server = _socketio()
    if server is not None:
        try:
            server.emit('notification', {
                'id': notification.id,
                'type': notification_type,
                'title': title,
                'message': message,
                'patient_id': patient.id if patient else None,
                'patient_name': patient.full_name if patient else None,
                'created_at': notification.created_at.isoformat(),
            }, to=f'staff_{staff.id}')
        except Exception as exc:                       # pragma: no cover
            log.error("Failed to push notification to staff %s: %s",
                      staff.id, exc)
    return notification


def send_assignment_notifications(patient, old_doctor, new_doctor,
                                  old_nurse, new_nurse, reason):
    """Tell the incoming *and* outgoing staff that the assignment changed.

    Both sides are always informed.  This is what guarantees that a doctor who
    missed alerts still receives the "patient re-routed" message even though
    they are no longer the assigned doctor.
    """
    label = f"{patient.full_name} ({patient.patient_id})"

    def _describe(role, old, new):
        if old is None and new is None:
            return None
        if old is None:
            return f"NEW {role}: you are now covering {label}."
        if new is None:
            return f"RELEASED {role}: you are no longer covering {label}."
        return (f"REROUTED {role}: {label} has moved from "
                f"{'Dr. ' if role == ROLE_DOCTOR else ''}{old.full_name} to "
                f"{'Dr. ' if role == ROLE_DOCTOR else ''}{new.full_name}.")

    messages = (
        (new_doctor, _describe(ROLE_DOCTOR, old_doctor, new_doctor)),
        (old_doctor, _describe(ROLE_DOCTOR, old_doctor, new_doctor)),
        (new_nurse, _describe(ROLE_NURSE, old_nurse, new_nurse)),
        (old_nurse, _describe(ROLE_NURSE, old_nurse, new_nurse)),
    )

    created = []
    for staff, body in messages:
        if staff is None or not body:
            continue
        # Do not double-notify when a role did not actually change.
        created.append(notify_staff(
            staff, f'Patient assignment: {patient.full_name}',
            f'{body} Reason: {reason}',
            patient=patient,
            notification_type='rerouting' if 'REROUTED' in body else 'assignment',
        ))
    return created



# ===========================================================================
# Alert distribution
# ===========================================================================
def alert_recipients(patient):
    """The assigned doctor and nurse - the only people an alert is sent to.

    Both roles always receive the alert so that, between them, they cover the
    patient.  Extra staff are deliberately *not* copied in: broadcasting to
    the whole shift is exactly what produced the alert flood this replaces.
    """
    recipients = []
    if patient.assigned_doctor and patient.assigned_doctor.is_active:
        recipients.append(patient.assigned_doctor)
    if patient.assigned_nurse and patient.assigned_nurse.is_active:
        recipients.append(patient.assigned_nurse)
    return recipients


def next_sequence_number():
    """Monotonic counter giving every alert a stable position in the feed."""
    last = db.session.query(db.func.max(Alert.sequence_no)).scalar()
    return (last or 0) + 1


def create_patient_alert(patient, vital, severity, title, message):
    """Create a single consolidated alert for a patient.
    Returns the alert object.
    """
    now = datetime.now()
    alert = Alert(
        patient_id=patient.id,
        vital_sign_id=vital.id if vital else None,
        alert_type='critical_vitals' if severity == 'critical' else 'vital_warning',
        severity=severity,
        title=title,
        message=message,
        is_acknowledged=False,
        ack_deadline=now + timedelta(seconds=ACK_WINDOW_SECONDS),
        sequence_no=next_sequence_number(),
    )
    db.session.add(alert)
    db.session.flush()
    return alert


def _publish_alert(alert, patient, recipients):
    """Push an alert only to assigned staff with an open dashboard."""
    server = _socketio()
    if server is None:
        return
    open_staff_ids = set(open_dashboards())

    payload = {
        'id': alert.id,
        'sequence_no': alert.sequence_no,
        'patient_id': patient.id,
        'patient_name': patient.full_name,
        'patient_code': patient.patient_id,
        'title': alert.title,
        'message': alert.message,
        'severity': alert.severity,
        'room': patient.room_number,
        'bed': patient.bed_number,
        'ack_deadline': alert.ack_deadline.isoformat() if alert.ack_deadline else None,
    }
    for staff in recipients:
        if staff.role not in (ROLE_DOCTOR, ROLE_NURSE) \
                or staff.id not in open_staff_ids:
            continue
        try:
            server.emit('new_alert', payload, to=f'staff_{staff.id}')
        except Exception as exc:                       # pragma: no cover
            log.error("Failed to publish alert %s to staff %s: %s",
                      alert.id, staff.id, exc)


def acknowledge_alert(alert, staff):
    """Record a staff member's response to an alert.

    The response is attributed to the *role* that staff member holds for this
    patient and clears that role's miss streak.  The alert only closes once
    both the doctor and the nurse have responded, which is what makes the
    "each role must answer" rule real rather than cosmetic.
    """
    now = datetime.now()
    patient = alert.patient
    is_doctor = patient_has_doctor(patient, staff)
    is_nurse = patient_has_nurse(patient, staff)

    if is_doctor:
        alert.doctor_ack_at = alert.doctor_ack_at or now
        patient.doctor_miss_streak = 0
    if is_nurse:
        alert.nurse_ack_at = alert.nurse_ack_at or now
        patient.nurse_miss_streak = 0
    if not (is_doctor or is_nurse):
        # An admin acknowledging on someone's behalf closes it outright.
        alert.acknowledged_by_id = staff.id
        alert.acknowledged_at = now
        alert.doctor_ack_at = alert.doctor_ack_at or now
        alert.nurse_ack_at = alert.nurse_ack_at or now
        patient.doctor_miss_streak = 0
        patient.nurse_miss_streak = 0

    if alert.doctor_ack_at and alert.nurse_ack_at:
        alert.is_acknowledged = True
        alert.acknowledged_by_id = alert.acknowledged_by_id or staff.id
        alert.acknowledged_at = alert.acknowledged_at or now
    return alert


def staff_can_see_alert(staff, alert):
    """Alerts are private to the assigned doctor and nurse of that patient."""
    patient = alert.patient
    return staff.role == 'admin' or patient_has_doctor(patient, staff) \
        or patient_has_nurse(patient, staff)


# ===========================================================================
# Re-routing
# ===========================================================================
def reroute_patient(patient, alert_ids=None, reason='manual re-route',
                    only_role=None, router=None):
    """Move a patient to a new doctor and/or nurse and inform **both** sides.

    ``only_role`` restricts the move to a single role ('doctor' or 'nurse'),
    which is what the missed-alert monitor uses: a doctor who misses alerts
    gets a new doctor, while the nurse stays exactly where they were.
    """
    router = router or PatientRouter()
    alert_ids = alert_ids or []
    old_doctor = patient.assigned_doctor
    old_nurse = patient.assigned_nurse

    exclude = set()
    if old_doctor:
        exclude.add(old_doctor.id)
    if old_nurse:
        exclude.add(old_nurse.id)

    new_doctor = None if only_role == ROLE_NURSE else router._pick(
        ROLE_DOCTOR, patient, exclude_ids=exclude)
    new_nurse = None if only_role == ROLE_DOCTOR else router._pick(
        ROLE_NURSE, patient, exclude_ids=exclude)

    # Fall back to keeping the current holder if nobody else is available.
    if new_doctor is None and only_role != ROLE_NURSE:
        new_doctor = old_doctor
    if new_nurse is None and only_role != ROLE_DOCTOR:
        new_nurse = old_nurse

    doctor_changed = bool(old_doctor and new_doctor and old_doctor.id != new_doctor.id)
    nurse_changed = bool(old_nurse and new_nurse and old_nurse.id != new_nurse.id)

    if not doctor_changed and not nurse_changed:
        log.warning("No alternative %s available to re-route %s",
                    only_role or 'staff', patient.full_name)
        return None

    patient.assigned_doctor_id = new_doctor.id if new_doctor else patient.assigned_doctor_id
    patient.assigned_nurse_id = new_nurse.id if new_nurse else patient.assigned_nurse_id
    patient.routed_at = datetime.now()
    # Reset only the streak of the role that actually moved.
    if doctor_changed:
        patient.doctor_miss_streak = 0
    if nurse_changed:
        patient.nurse_miss_streak = 0
    db.session.flush()

    log_row = AlertRerouting(
        patient_id=patient.id,
        old_doctor_id=old_doctor.id if old_doctor else None,
        old_nurse_id=old_nurse.id if old_nurse else None,
        new_doctor_id=new_doctor.id if new_doctor else None,
        new_nurse_id=new_nurse.id if new_nurse else None,
        triggering_alert_ids=json.dumps(alert_ids),
        reason=reason,
    )
    db.session.add(log_row)
    db.session.flush()

    # Both the outgoing and the incoming staff are told what happened.
    send_assignment_notifications(patient, old_doctor, new_doctor,
                                  old_nurse, new_nurse, reason)
    log_row.notified_old_staff = True
    log_row.notified_new_staff = True

    moved = ', '.join(log_row.changed_roles) or 'staff'
    log.warning("Re-routed %s -> %s (%s)", patient.full_name, moved, reason)

    server = _socketio()
    if server is not None:
        try:
            server.emit('patient_rerouted', {
                'patient_id': patient.id,
                'patient_name': patient.full_name,
                'patient_code': patient.patient_id,
                'roles': log_row.changed_roles,
                'old_doctor': old_doctor.full_name if doctor_changed else None,
                'new_doctor': new_doctor.full_name if doctor_changed else None,
                'old_nurse': old_nurse.full_name if nurse_changed else None,
                'new_nurse': new_nurse.full_name if nurse_changed else None,
                'reason': reason,
            })
        except Exception as exc:                       # pragma: no cover
            log.error("Failed to broadcast re-route: %s", exc)

    db.session.commit()
    return log_row


def recent_unacknowledged_alert(patient, cooldown=None):
    """Latest still-open alert for a patient, used to suppress duplicates."""
    cooldown = ALERT_COOLDOWN_SECONDS if cooldown is None else cooldown
    cutoff = datetime.now() - timedelta(seconds=cooldown)
    return Alert.query.filter(
        Alert.patient_id == patient.id,
        Alert.is_acknowledged.is_(False),
        Alert.created_at >= cutoff,
    ).order_by(Alert.created_at.desc()).first()


def process_missed_alerts(router=None):
    """Re-route staff who let ``MAX_CONSECUTIVE_MISSES`` alerts lapse in a row.

    Doctor and nurse streaks are evaluated **independently**:

    * the doctor missed twice -> only the doctor is replaced
    * the nurse missed twice  -> only the nurse is replaced
    * both missed             -> both are replaced

    Returns the list of ``AlertRerouting`` rows created by this pass.
    """
    router = router or PatientRouter()
    now = datetime.now()
    reroutings = []

    # Alerts whose acknowledgement window has closed with at least one role
    # silent.
    expired = Alert.query.filter(
        Alert.ack_deadline.isnot(None),
        Alert.ack_deadline < now,
    ).order_by(Alert.ack_deadline.asc()).limit(200).all()

    for alert in expired:
        patient = alert.patient
        if patient is None or patient.status == 'discharged':
            continue

        doctor_missed = alert.doctor_ack_at is None
        nurse_missed = alert.nurse_ack_at is None
        if not (doctor_missed or nurse_missed):
            continue

        if doctor_missed:
            patient.doctor_miss_streak = (patient.doctor_miss_streak or 0) + 1
        if nurse_missed:
            patient.nurse_miss_streak = (patient.nurse_miss_streak or 0) + 1

        log.info("Missed alert %s for %s | doctor streak=%d, nurse streak=%d",
                 alert.id, patient.full_name,
                 patient.doctor_miss_streak, patient.nurse_miss_streak)

        doctor_due = doctor_missed and patient.doctor_miss_streak >= MAX_CONSECUTIVE_MISSES
        nurse_due = nurse_missed and patient.nurse_miss_streak >= MAX_CONSECUTIVE_MISSES

        if not (doctor_due or nurse_due):
            # Counted, but below the threshold: close it so the same lapse is
            # never tallied twice.
            alert.is_acknowledged = True
            alert.acknowledged_at = now
            db.session.commit()
            continue

        roles = ([ROLE_DOCTOR] if doctor_due else []) + \
                ([ROLE_NURSE] if nurse_due else [])
        reason = (f"{MAX_CONSECUTIVE_MISSES} consecutive alerts were not "
                  f"acknowledged by the {' and '.join(roles)}")

        if len(roles) == 2:
            row = reroute_patient(patient, [alert.id], reason=reason, router=router)
        else:
            row = reroute_patient(patient, [alert.id], reason=reason,
                                  only_role=roles[0], router=router)
        if row is not None:
            reroutings.append(row)

        # The re-route already acted on this alert, so close it either way.
        alert.is_acknowledged = True
        alert.acknowledged_at = now
        db.session.commit()

    return reroutings


router = PatientRouter()


# ===========================================================================
# Monitoring - dashboard presence tracking
# ===========================================================================
#: staff_id -> last time that member's dashboard was seen (unix timestamp).
#: The monitor refuses to generate anything while this mapping is empty, which
#: is what "alerts are only produced when a dashboard is open" means in code.
_dashboard_last_seen = {}
_dashboard_lock = threading.Lock()
_monitor_thread = None
_monitor_stop = threading.Event()


def mark_dashboard_open(staff_id):
    """Called whenever a staff dashboard connects or sends a heartbeat."""
    with _dashboard_lock:
        _dashboard_last_seen[staff_id] = time.time()


def mark_dashboard_closed(staff_id):
    with _dashboard_lock:
        _dashboard_last_seen.pop(staff_id, None)


def open_dashboards(stale_after=None):
    """Staff ids with a currently-open dashboard, dropping stale heartbeats."""
    stale_after = stale_after or (MONITOR_INTERVAL_SECONDS * 3)
    cutoff = time.time() - stale_after
    with _dashboard_lock:
        stale = [sid for sid, seen in _dashboard_last_seen.items() if seen < cutoff]
        for sid in stale:
            _dashboard_last_seen.pop(sid, None)
        return sorted(_dashboard_last_seen)


def dashboards_open():
    """True when at least one staff dashboard is currently being watched."""
    return bool(open_dashboards())


def evaluate_vitals(vital, patient=None):
    """Return ``(severity, [breach strings])`` for a vitals reading.

    A reading yields a single consolidated verdict.  ``None`` severity means
    nothing needs alerting, which is what keeps the alert volume low.
    """
    if vital is None:
        return None, []
    from services import VITAL_RANGES

    breaches = []
    is_critical = False

    # Check Heart Rate
    if vital.heart_rate is not None:
        low, high, unit = VITAL_RANGES['heart_rate']
        if vital.heart_rate < 50 or vital.heart_rate > 120:
            is_critical = True
            breaches.append(f"Heart rate {vital.heart_rate}{unit} (Critical: <50 or >120)")
        elif vital.heart_rate < low or vital.heart_rate > high:
            breaches.append(f"Heart rate {vital.heart_rate}{unit} (Normal: {low}-{high})")

    # Check Blood Pressure
    if vital.blood_pressure_systolic is not None:
        low, high, unit = VITAL_RANGES['blood_pressure_systolic']
        if vital.blood_pressure_systolic < 80 or vital.blood_pressure_systolic > 180:
            is_critical = True
            breaches.append(f"Systolic BP {vital.blood_pressure_systolic}{unit} (Critical: <80 or >180)")
        elif vital.blood_pressure_systolic < low or vital.blood_pressure_systolic > high:
            breaches.append(f"Systolic BP {vital.blood_pressure_systolic}{unit} (Normal: {low}-{high})")

    if vital.blood_pressure_diastolic is not None:
        low, high, unit = VITAL_RANGES['blood_pressure_diastolic']
        if vital.blood_pressure_diastolic < 50 or vital.blood_pressure_diastolic > 110:
            is_critical = True
            breaches.append(f"Diastolic BP {vital.blood_pressure_diastolic}{unit} (Critical: <50 or >110)")
        elif vital.blood_pressure_diastolic < low or vital.blood_pressure_diastolic > high:
            breaches.append(f"Diastolic BP {vital.blood_pressure_diastolic}{unit} (Normal: {low}-{high})")

    # Check Oxygen Saturation
    if vital.oxygen_saturation is not None:
        low, high, unit = VITAL_RANGES['oxygen_saturation']
        if vital.oxygen_saturation < 90:
            is_critical = True
            breaches.append(f"Oxygen saturation {vital.oxygen_saturation}{unit} (Critical: <90%)")
        elif vital.oxygen_saturation < low:
            breaches.append(f"Oxygen saturation {vital.oxygen_saturation}{unit} (Normal: {low}-{high}%)")

    # Check Temperature
    if vital.temperature is not None:
        low, high, unit = VITAL_RANGES['temperature']
        if vital.temperature < 95.0 or vital.temperature > 103.0:
            is_critical = True
            breaches.append(f"Temperature {vital.temperature}{unit} (Critical: <95 or >103)")
        elif vital.temperature < low or vital.temperature > high:
            breaches.append(f"Temperature {vital.temperature}{unit} (Normal: {low}-{high})")

    # Check Respiratory Rate
    if vital.respiratory_rate is not None:
        low, high, unit = VITAL_RANGES['respiratory_rate']
        if vital.respiratory_rate < 8 or vital.respiratory_rate > 30:
            is_critical = True
            breaches.append(f"Respiratory rate {vital.respiratory_rate}{unit} (Critical: <8 or >30)")
        elif vital.respiratory_rate < low or vital.respiratory_rate > high:
            breaches.append(f"Respiratory rate {vital.respiratory_rate}{unit} (Normal: {low}-{high})")

    if not breaches:
        return None, []

    severity = 'critical' if is_critical else 'warning'
    return severity, breaches



def sample_patient_vitals(patient, force_critical=False):
    """Record one vitals reading for a patient using the shared generator."""
    from services import generate_vital_sign

    status_bias = None
    if force_critical:
        status_bias = 'critical'
    elif patient.status == 'icu':
        status_bias = random.choices(['critical', 'warning', None], weights=[15, 25, 60])[0]
    elif patient.status == 'emergency':
        status_bias = random.choices(['critical', 'warning', None], weights=[20, 30, 50])[0]
    else:
        status_bias = random.choices(['critical', 'warning', None], weights=[3, 7, 90])[0]

    vital = generate_vital_sign(patient, status_bias)
    db.session.add(vital)
    db.session.commit()

    server = _socketio()
    if server is not None:
        payload = {
            'patient_id': patient.id,
            'heart_rate': vital.heart_rate,
            'bp_systolic': vital.blood_pressure_systolic,
            'bp_diastolic': vital.blood_pressure_diastolic,
            'oxygen': vital.oxygen_saturation,
            'temperature': vital.temperature,
            'respiratory_rate': vital.respiratory_rate,
            'status': vital.status,
            'timestamp': vital.recorded_at.strftime('%H:%M:%S'),
        }
        for room in (patient.assigned_doctor_id, patient.assigned_nurse_id):
            if room:
                try:
                    server.emit('vital_update', payload, to=f'staff_{room}')
                except Exception:                       # pragma: no cover
                    pass
    return vital


def run_monitor_cycle():
    """One monitor pass: sample vitals, then act on any breaches.

    Returns the alerts created during this pass.  Does nothing at all when no
    dashboard is open, so an idle server produces no alert noise.
    """
    if not dashboards_open():
        return []

    from services import send_to_n8n_webhook

    patients = PatientRouter.active_patients()
    if not patients:
        return []

    sample_size = max(1, int(len(patients) * VITALS_SAMPLE_FRACTION))
    chosen = (patients if sample_size >= len(patients)
              else random.sample(patients, sample_size))

    created = []
    for patient in chosen:
        vital = sample_patient_vitals(patient)
        severity, breaches = evaluate_vitals(vital, patient)
        if not severity or not breaches:
            continue

        # One consolidated alert per patient, and never two inside the
        # cooldown window - this is the "fewer alerts" rule.
        if recent_unacknowledged_alert(patient):
            continue

        headline = breaches[0].split(' (')[0]
        title = ('CRITICAL: ' if severity == 'critical' else 'Warning: ') + \
                f"{patient.full_name} - {headline}"
        message = f"{patient.location_label}. {'; '.join(breaches)}."

        alert, _recipients = create_patient_alert(
            patient, vital, severity, title, message)
        _publish_alert(alert, patient, _recipients)
        created.append(alert)

        try:
            send_to_n8n_webhook(alert)
        except Exception:                              # pragma: no cover
            pass

    if created:
        db.session.commit()
    return created


def _app_context():
    """Import the Flask app lazily to avoid a circular import at module load."""
    from app import app
    return app.app_context()


def monitor_loop():
    """Background worker: keeps the ward alive while dashboards are watched."""
    log.info("Monitor started (interval=%ss, min patients/doctor=%d)",
             MONITOR_INTERVAL_SECONDS, MIN_PATIENTS_PER_DOCTOR)
    while not _monitor_stop.is_set():
        try:
            with _app_context():
                process_missed_alerts()
                run_monitor_cycle()
        except Exception as exc:
            log.error("Monitor cycle failed: %s", exc, exc_info=True)
        finally:
            db.session.remove()
        _monitor_stop.wait(MONITOR_INTERVAL_SECONDS)
    log.info("Monitor stopped")


def start_monitor():
    """Start the background monitor exactly once."""
    global _monitor_thread
    if _monitor_thread and _monitor_thread.is_alive():
        return _monitor_thread
    _monitor_stop.clear()
    _monitor_thread = threading.Thread(
        target=monitor_loop, name='caresync-monitor', daemon=True)
    _monitor_thread.start()
    return _monitor_thread


def stop_monitor():
    """Signal the background monitor to exit (used on shutdown)."""
    _monitor_stop.set()


def monitoring_status():
    """Snapshot for the admin dashboard and the health endpoint."""
    return {
        'dashboards_open': open_dashboards(),
        'min_patients_per_doctor': MIN_PATIENTS_PER_DOCTOR,
        'max_consecutive_misses': MAX_CONSECUTIVE_MISSES,
        'ack_window_seconds': ACK_WINDOW_SECONDS,
        'interval_seconds': MONITOR_INTERVAL_SECONDS,
        'monitoring': bool(_monitor_thread and _monitor_thread.is_alive()),
    }


def workload_report():
    """Per-doctor patient counts, used by the admin portal and the CLI."""
    doctors = StaffMember.query.filter(
        StaffMember.role == ROLE_DOCTOR,
        StaffMember.is_active.is_(True),
    ).order_by(StaffMember.staff_id).all()

    rows = []
    for doctor in doctors:
        load = PatientRouter.load_of(doctor)
        rows.append({
            'staff': doctor,
            'patients': load,
            'meets_minimum': load >= MIN_PATIENTS_PER_DOCTOR,
        })

    nurses = StaffMember.query.filter(
        StaffMember.role == ROLE_NURSE,
        StaffMember.is_active.is_(True),
    ).order_by(StaffMember.staff_id).all()
    nurse_rows = [{
        'staff': nurse,
        'patients': PatientRouter.load_of(nurse),
    } for nurse in nurses]

    return {
        'doctors': rows,
        'nurses': nurse_rows,
        'minimum': MIN_PATIENTS_PER_DOCTOR,
        'short_fall': [r for r in rows if not r['meets_minimum']],
    }
