import csv
import logging
import re
from pathlib import Path
import numpy as np
from datetime import datetime, timedelta
from sklearn.ensemble import IsolationForest, RandomForestClassifier
from sklearn.preprocessing import StandardScaler

logging.basicConfig(level=logging.DEBUG)

class RiskPredictor:
    def __init__(self):
        self.scaler = StandardScaler()
        self.anomaly_detector = IsolationForest(contamination=0.1, random_state=42)
        self.risk_classifier = RandomForestClassifier(n_estimators=50, random_state=42)
        self.is_trained = False
        self.training_metadata = {}

    def train_from_csv(self, data_path):
        """Train the demo classifier from a labeled vital-sign CSV.

        The bundled CSV contains synthetic observations only.  It is useful for
        exercising the application, but must not be treated as a clinically
        validated model or trained on real patient data without governance.
        """
        required_columns = (
            'heart_rate', 'blood_pressure_systolic',
            'blood_pressure_diastolic', 'oxygen_saturation',
            'temperature', 'respiratory_rate', 'risk_label'
        )
        data_path = Path(data_path)
        if not data_path.is_file():
            raise FileNotFoundError(f'Training data was not found: {data_path}')

        feature_rows = []
        labels = []
        with data_path.open(newline='', encoding='utf-8') as source:
            reader = csv.DictReader(source)
            field_names = set(reader.fieldnames or [])
            missing_columns = set(required_columns) - field_names
            if missing_columns:
                missing = ', '.join(sorted(missing_columns))
                raise ValueError(f'Training data is missing required columns: {missing}')

            for row_number, row in enumerate(reader, start=2):
                try:
                    values = [float(row[column]) for column in required_columns[:-1]]
                    label = int(row['risk_label'])
                except (TypeError, ValueError) as exc:
                    raise ValueError(f'Invalid training value on row {row_number}') from exc

                if label not in (0, 1):
                    raise ValueError(f'Risk label on row {row_number} must be 0 or 1')
                if not all(np.isfinite(value) for value in values):
                    raise ValueError(f'Non-finite training value on row {row_number}')

                feature_rows.append(values)
                labels.append(label)

        if len(feature_rows) < 20:
            raise ValueError('At least 20 labeled observations are required for training')
        if len(set(labels)) != 2:
            raise ValueError('Training data must include both stable (0) and high-risk (1) labels')

        features = np.asarray(feature_rows, dtype=float)
        targets = np.asarray(labels, dtype=int)
        scaled_features = self.scaler.fit_transform(features)
        self.anomaly_detector.fit(scaled_features)
        self.risk_classifier.fit(scaled_features, targets)
        self.is_trained = True
        self.training_metadata = {
            'source': str(data_path),
            'sample_count': len(feature_rows),
            'class_counts': {
                'stable': int(np.count_nonzero(targets == 0)),
                'high_risk': int(np.count_nonzero(targets == 1)),
            },
        }
        return self.training_metadata

    def predict_model_risk_probability(self, vital):
        """Return the demo model's high-risk probability for a single vital set.

        This is deliberately supplemental: clinical alerts continue to use the
        transparent threshold-and-trend logic below.
        """
        if not self.is_trained:
            return None

        features = self.prepare_vital_features([vital])
        if features.size == 0:
            return None

        scaled_features = self.scaler.transform(features)
        classes = list(self.risk_classifier.classes_)
        if 1 not in classes:
            return None
        probability_index = classes.index(1)
        probability = self.risk_classifier.predict_proba(scaled_features)[0][probability_index]
        return round(float(probability), 3)
        
    def prepare_vital_features(self, vitals):
        features = []
        for vital in vitals:
            feature_vector = [
                vital.heart_rate or 75,
                vital.blood_pressure_systolic or 120,
                vital.blood_pressure_diastolic or 80,
                vital.oxygen_saturation or 98,
                vital.temperature or 98.6,
                vital.respiratory_rate or 16
            ]
            features.append(feature_vector)
        return np.array(features) if features else np.array([])
    
    def calculate_trend(self, values, window=5):
        if len(values) < 2:
            return 0
        values = values[-window:]
        if len(values) < 2:
            return 0
        x = np.arange(len(values))
        slope = np.polyfit(x, values, 1)[0]
        return slope
    
    def analyze_patient_risk(self, patient_id):
        from models import VitalSign
        vitals = VitalSign.query.filter_by(patient_id=patient_id).order_by(
            VitalSign.recorded_at.desc()
        ).limit(20).all()
        
        if len(vitals) < 3:
            return {
                'risk_level': 'unknown',
                'risk_score': 0,
                'predictions': [],
                'message': 'Insufficient data for analysis'
            }
        
        latest = vitals[0]
        features = self.prepare_vital_features(vitals)
        
        if len(features) == 0:
            return {
                'risk_level': 'unknown',
                'risk_score': 0,
                'predictions': [],
                'message': 'No vital data available'
            }

        model_risk_probability = self.predict_model_risk_probability(latest)
        
        risk_factors = []
        risk_score = 0
        
        hr_values = [v.heart_rate for v in vitals if v.heart_rate]
        if hr_values:
            hr_trend = self.calculate_trend(hr_values)
            hr_avg = np.mean(hr_values)
            hr_std = np.std(hr_values)
            
            if hr_avg < 55 or hr_avg > 110:
                risk_score += 25
                risk_factors.append({
                    'type': 'heart_rate',
                    'severity': 'high',
                    'message': f'Average heart rate ({hr_avg:.0f} bpm) is outside normal range',
                    'trend': 'increasing' if hr_trend > 0.5 else 'decreasing' if hr_trend < -0.5 else 'stable'
                })
            elif hr_avg < 60 or hr_avg > 100:
                risk_score += 10
                risk_factors.append({
                    'type': 'heart_rate',
                    'severity': 'medium',
                    'message': f'Heart rate trending towards abnormal ({hr_avg:.0f} bpm)',
                    'trend': 'increasing' if hr_trend > 0.5 else 'decreasing' if hr_trend < -0.5 else 'stable'
                })
            
            if hr_std > 15:
                risk_score += 15
                risk_factors.append({
                    'type': 'heart_rate_variability',
                    'severity': 'medium',
                    'message': f'High heart rate variability detected (SD: {hr_std:.1f})',
                    'trend': 'unstable'
                })
        
        bp_systolic = [v.blood_pressure_systolic for v in vitals if v.blood_pressure_systolic]
        if bp_systolic:
            bp_trend = self.calculate_trend(bp_systolic)
            bp_avg = np.mean(bp_systolic)
            
            if bp_avg < 85 or bp_avg > 165:
                risk_score += 30
                risk_factors.append({
                    'type': 'blood_pressure',
                    'severity': 'high',
                    'message': f'Blood pressure ({bp_avg:.0f} mmHg systolic) critically abnormal',
                    'trend': 'increasing' if bp_trend > 1 else 'decreasing' if bp_trend < -1 else 'stable'
                })
            elif bp_avg < 95 or bp_avg > 145:
                risk_score += 15
                risk_factors.append({
                    'type': 'blood_pressure',
                    'severity': 'medium',
                    'message': f'Blood pressure trending towards abnormal range',
                    'trend': 'increasing' if bp_trend > 1 else 'decreasing' if bp_trend < -1 else 'stable'
                })
        
        oxygen_values = [v.oxygen_saturation for v in vitals if v.oxygen_saturation]
        if oxygen_values:
            o2_trend = self.calculate_trend(oxygen_values)
            o2_avg = np.mean(oxygen_values)
            
            if o2_avg < 90:
                risk_score += 35
                risk_factors.append({
                    'type': 'oxygen_saturation',
                    'severity': 'critical',
                    'message': f'Oxygen saturation critically low ({o2_avg:.0f}%)',
                    'trend': 'decreasing' if o2_trend < -0.2 else 'stable'
                })
            elif o2_avg < 94:
                risk_score += 20
                risk_factors.append({
                    'type': 'oxygen_saturation',
                    'severity': 'high',
                    'message': f'Low oxygen saturation ({o2_avg:.0f}%)',
                    'trend': 'decreasing' if o2_trend < -0.2 else 'stable'
                })
            
            if o2_trend < -0.5 and o2_avg < 96:
                risk_score += 10
                risk_factors.append({
                    'type': 'oxygen_trend',
                    'severity': 'medium',
                    'message': 'Oxygen saturation showing declining trend',
                    'trend': 'decreasing'
                })
        
        temp_values = [v.temperature for v in vitals if v.temperature]
        if temp_values:
            temp_avg = np.mean(temp_values)
            temp_trend = self.calculate_trend(temp_values)
            
            if temp_avg < 96 or temp_avg > 102:
                risk_score += 25
                risk_factors.append({
                    'type': 'temperature',
                    'severity': 'high',
                    'message': f'Temperature critically abnormal ({temp_avg:.1f}°F)',
                    'trend': 'increasing' if temp_trend > 0.1 else 'decreasing' if temp_trend < -0.1 else 'stable'
                })
            elif temp_avg < 97 or temp_avg > 100:
                risk_score += 10
                risk_factors.append({
                    'type': 'temperature',
                    'severity': 'medium',
                    'message': f'Temperature outside normal range ({temp_avg:.1f}°F)',
                    'trend': 'increasing' if temp_trend > 0.1 else 'decreasing' if temp_trend < -0.1 else 'stable'
                })
        
        resp_values = [v.respiratory_rate for v in vitals if v.respiratory_rate]
        if resp_values:
            resp_avg = np.mean(resp_values)
            
            if resp_avg < 10 or resp_avg > 28:
                risk_score += 25
                risk_factors.append({
                    'type': 'respiratory_rate',
                    'severity': 'high',
                    'message': f'Respiratory rate abnormal ({resp_avg:.0f}/min)',
                    'trend': 'abnormal'
                })
        
        if risk_score >= 70:
            risk_level = 'critical'
        elif risk_score >= 50:
            risk_level = 'high'
        elif risk_score >= 30:
            risk_level = 'moderate'
        elif risk_score > 0:
            risk_level = 'low'
        else:
            risk_level = 'stable'
        
        predictions = []
        for factor in risk_factors:
            if factor['trend'] in ['increasing', 'decreasing']:
                if factor['type'] == 'heart_rate' and factor['trend'] == 'increasing':
                    predictions.append('Heart rate may reach critical levels in next 1-2 hours')
                elif factor['type'] == 'oxygen_saturation' and factor['trend'] == 'decreasing':
                    predictions.append('Oxygen levels may require intervention within 30-60 minutes')
                elif factor['type'] == 'blood_pressure' and factor['trend'] == 'increasing':
                    predictions.append('Blood pressure trending upward - monitor closely')
                elif factor['type'] == 'temperature' and factor['trend'] == 'increasing':
                    predictions.append('Fever may worsen - consider intervention')
        
        # Optionally consult AI (Groq) to refine/override assessment
        try:
            patient = Patient.query.get(patient_id)
            ai_advice = self.ai_consult(patient, vitals, risk_score, risk_factors)
            if ai_advice:
                # Apply suggested numeric score if provided
                if 'suggested_score' in ai_advice:
                    try:
                        risk_score = int(ai_advice['suggested_score'])
                    except Exception:
                        pass
                # Apply suggested level if provided
                if 'suggested_level' in ai_advice:
                    risk_level = ai_advice['suggested_level']

                # Attach AI note to predictions for visibility in UI
                note = ai_advice.get('note')
                if note:
                    predictions.insert(0, f"AI: {note}")
        except Exception as e:
            logging.debug(f"AI advice skipped due to error: {e}")

        return {
            'risk_level': risk_level,
            'risk_score': min(int(risk_score), 100),
            'risk_factors': risk_factors,
            'predictions': predictions,
            'model_risk_probability': model_risk_probability,
            'model_training_samples': self.training_metadata.get('sample_count', 0),
            'analyzed_at': datetime.now().isoformat(),
            'vital_count': len(vitals)
        }

    def ai_consult(self, patient, vitals, current_score, risk_factors):
        """Optionally consult Groq for an AI opinion on risk.
        Returns a dict with optional 'suggested_level' and 'suggested_score' and a note.
        This call is best-effort and will not raise on error.
        """
        from app import generate_ai_response, groq_client
        if groq_client is None:
            return None

        try:
            # Build a concise prompt summarizing patient and recent vitals
            vitals_summary = []
            for v in vitals[:10]:
                t = v.recorded_at.isoformat() if v.recorded_at else ''
                vitals_summary.append(f"{t}: HR={v.heart_rate or 'n/a'}, BP={v.blood_pressure_systolic or 'n/a'}/{v.blood_pressure_diastolic or 'n/a'}, O2={v.oxygen_saturation or 'n/a'}%, Temp={v.temperature or 'n/a'}F, RR={v.respiratory_rate or 'n/a'}")

            prompt = (
                f"You are a clinical risk assistant. Evaluate the following patient vitals and the computed risk score {current_score}. "
                f"Patient: {patient.full_name} (ID: {patient.patient_id}), age={patient.age}, status={patient.status}.\n"
                f"Recent vitals (most recent first):\n" + "\n".join(vitals_summary) + "\n"
                f"Risk factors: {risk_factors}\n"
                "Provide a short recommendation in one line and optionally suggest a risk level (critical/high/moderate/low/stable) and a suggested numeric score (0-100)."
            )

            resp_text = generate_ai_response([
                {'role': 'user', 'content': prompt}
            ])

            if not resp_text:
                return None

            # Parse suggested level and numeric score if present
            suggested = {}
            txt = resp_text.lower()
            if 'critical' in txt:
                suggested['suggested_level'] = 'critical'
            elif 'high' in txt:
                suggested['suggested_level'] = 'high'
            elif 'moderate' in txt:
                suggested['suggested_level'] = 'moderate'
            elif 'low' in txt:
                suggested['suggested_level'] = 'low'
            elif 'stable' in txt:
                suggested['suggested_level'] = 'stable'

            m = re.search(r"(score|score:?)\s*(\d{1,3})", txt)
            if m:
                try:
                    suggested['suggested_score'] = max(0, min(100, int(m.group(2))))
                except Exception:
                    pass

            suggested['note'] = resp_text.strip()[:1000]
            return suggested
        except Exception as e:
            logging.error(f"AI consult failed: {e}")
            return None
    
    def get_early_warning_score(self, vital):
        score = 0
        
        if vital.respiratory_rate:
            if vital.respiratory_rate <= 8:
                score += 3
            elif vital.respiratory_rate <= 11:
                score += 1
            elif vital.respiratory_rate >= 25:
                score += 3
            elif vital.respiratory_rate >= 21:
                score += 2
        
        if vital.oxygen_saturation:
            if vital.oxygen_saturation <= 91:
                score += 3
            elif vital.oxygen_saturation <= 93:
                score += 2
            elif vital.oxygen_saturation <= 95:
                score += 1
        
        if vital.heart_rate:
            if vital.heart_rate <= 40:
                score += 3
            elif vital.heart_rate <= 50:
                score += 1
            elif vital.heart_rate >= 131:
                score += 3
            elif vital.heart_rate >= 111:
                score += 2
            elif vital.heart_rate >= 91:
                score += 1
        
        if vital.blood_pressure_systolic:
            if vital.blood_pressure_systolic <= 90:
                score += 3
            elif vital.blood_pressure_systolic <= 100:
                score += 2
            elif vital.blood_pressure_systolic >= 220:
                score += 3
        
        if vital.temperature:
            if vital.temperature <= 95:
                score += 3
            elif vital.temperature >= 102.2:
                score += 2
            elif vital.temperature >= 100.4:
                score += 1
        
        return score


def create_predictive_alert(patient_id, risk_analysis):
    # Only create alerts for high or critical risk to reduce noise
    from app import db
    from models import Patient, Alert
    from alert_router import send_to_n8n_webhook
    if risk_analysis['risk_level'] not in ['critical', 'high']:
        return
    
    patient = Patient.query.get(patient_id)
    if not patient:
        return
    
    existing = Alert.query.filter_by(
        patient_id=patient_id,
        alert_type='predictive_warning',
        is_acknowledged=False
    ).first()
    
    if existing:
        existing.message = f"Risk Score: {risk_analysis['risk_score']}. " + "; ".join(risk_analysis.get('predictions', []))
        existing.severity = 'critical' if risk_analysis['risk_level'] == 'critical' else 'warning'
        existing.created_at = datetime.now()
        db.session.commit()
        from routing import _publish_alert, alert_recipients
        _publish_alert(existing, patient, alert_recipients(patient))
        try:
            send_to_n8n_webhook(existing)
        except Exception:
            pass
    else:
        alert = Alert(
            patient_id=patient_id,
            alert_type='predictive_warning',
            severity='critical' if risk_analysis['risk_level'] == 'critical' else 'warning',
            title=f"AI Risk Alert - {patient.full_name}",
            message=f"Risk Score: {risk_analysis['risk_score']}. " + "; ".join(risk_analysis.get('predictions', ['Early intervention recommended']))
        )
        db.session.add(alert)
        db.session.commit()
        from routing import _publish_alert, alert_recipients
        _publish_alert(alert, patient, alert_recipients(patient))
        try:
            send_to_n8n_webhook(alert)
        except Exception:
            pass


risk_predictor = RiskPredictor()


def _train_bundled_demo_model():
    """Load the project-owned synthetic dataset for the non-clinical demo model."""
    data_path = Path(__file__).with_name('data') / 'demo_vitals_training.csv'
    try:
        metadata = risk_predictor.train_from_csv(data_path)
        logging.info(
            'Demo risk model trained from %s labeled observations',
            metadata['sample_count']
        )
    except Exception as exc:
        # The rule-based assessment remains available if optional demo data is
        # missing or invalid, so the application can still start safely.
        logging.warning('Demo risk model was not trained: %s', exc)


_train_bundled_demo_model()


def analyze_all_patients():
    # Use a simple, non-transactional approach to avoid SQLAlchemy context issues
    from models import Patient
    try:
        patients = Patient.query.filter(Patient.status.in_(['admitted', 'icu', 'emergency'])).all()
    except Exception as e:
        logging.error(f"Error fetching patients: {e}")
        return []

    results = []
    for patient in patients:
        try:
            pid = patient.id
            pname = f"{patient.first_name or ''} {patient.last_name or ''}".strip()
            room = patient.room_number or ''
            
            analysis = risk_predictor.analyze_patient_risk(pid)
            results.append({
                'patient_id': pid,
                'patient_name': pname,
                'room': room,
                **analysis
            })

            if analysis['risk_level'] in ['critical', 'high']:
                create_predictive_alert(pid, analysis)

        except Exception as e:
            logging.error(f"Error analyzing patient {pid}: {e}")

    return results
