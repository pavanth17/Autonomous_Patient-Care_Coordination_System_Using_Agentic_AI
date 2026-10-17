import pyttsx3
import logging
import threading
import time
import queue
from datetime import datetime

logger = logging.getLogger(__name__)

class EmergencyAnnouncer:
    """Automated emergency announcement system using text-to-speech.
    
    Refactored to run all TTS operations on a dedicated thread to ensure 
    compatibility with COM/threading models (especially on Windows).
    """

    def __init__(self):
        self.announcement_queue = queue.Queue()
        self.is_running = True
        self.worker_thread = threading.Thread(target=self._tts_worker, daemon=True)
        self.worker_thread.start()

    def _tts_worker(self):
        """Worker thread that owns the TTS engine lifecycle."""
        engine = None
        try:
            logger.info("Initializing TTS engine in worker thread...")
            engine = pyttsx3.init()
            
            # Configure voice settings
            voices = engine.getProperty('voices')
            if voices:
                for voice in voices:
                    if 'female' in voice.name.lower() or 'zira' in voice.name.lower():
                        engine.setProperty('voice', voice.id)
                        break
            
            engine.setProperty('rate', 160)  # Slower for clarity
            engine.setProperty('volume', 1.0) # Max volume
            logger.info("TTS Engine initialized successfully.")
            
        except Exception as e:
            logger.error(f"Failed to initialize TTS engine in worker: {e}")
            return

        while self.is_running:
            try:
                # Block until an announcement is available
                announcement = self.announcement_queue.get()
                
                if announcement is None: # Sentinel to stop
                    break
                
                text = announcement.get('text')
                if text:
                    logger.info(f"Announcing: {text}")
                    engine.say(text)
                    engine.runAndWait()
                    time.sleep(0.5)
                
                self.announcement_queue.task_done()
                
            except Exception as e:
                logger.error(f"Error in TTS worker loop: {e}")
                # Prevent tight loop if error persists
                time.sleep(1)

    def announce_emergency(self, patient_name, condition, room_number, bed_number=None, severity='critical'):
        """Announce an emergency situation"""
        # Create announcement text
        bed_info = f", bed {bed_number}" if bed_number else ""
        announcement_text = f"EMERGENCY ALERT! Patient {patient_name} in room {room_number}{bed_info}. {condition}. Immediate attention required!"

        item = {
            'text': announcement_text,
            'severity': severity,
            'timestamp': datetime.now()
        }
        
        # Add to thread-safe queue
        self.announcement_queue.put(item)
        logger.info(f"Emergency announcement queued: {announcement_text}")
        return True

    def announce_vital_alert(self, patient, vital_type, value, unit, room_number, bed_number=None):
        """Announce a specific vital sign alert"""
        condition_messages = {
            'heart_rate': {
                'low': f"Critical low heart rate of {value}",
                'high': f"Critical high heart rate of {value}"
            },
            'blood_pressure': {
                'low': f"Critical low blood pressure of {value}",
                'high': f"Critical high blood pressure of {value}"
            },
            'oxygen_saturation': {
                'low': f"Critical low oxygen saturation of {value} percent"
            },
            'temperature': {
                'low': f"Critical low temperature of {value}",
                'high': f"Critical high temperature of {value}"
            },
            'respiratory_rate': {
                'low': f"Critical low respiratory rate of {value}",
                'high': f"Critical high respiratory rate of {value}"
            }
        }

        # Determine if it's high or low
        if vital_type == 'heart_rate':
            condition = condition_messages[vital_type]['low'] if value < 50 else condition_messages[vital_type]['high']
        elif vital_type == 'blood_pressure':
            condition = condition_messages[vital_type]['low'] if value < 90 else condition_messages[vital_type]['high']
        elif vital_type == 'oxygen_saturation':
            condition = condition_messages[vital_type]['low']
        elif vital_type == 'temperature':
            condition = condition_messages[vital_type]['low'] if value < 96.5 else condition_messages[vital_type]['high']
        elif vital_type == 'respiratory_rate':
            condition = condition_messages[vital_type]['low'] if value < 10 else condition_messages[vital_type]['high']
        else:
            condition = f"Critical {vital_type} reading of {value} {unit}"

        return self.announce_emergency(patient.full_name, condition, room_number, bed_number, 'critical')

    def test_announcement(self):
        """Test the announcement system"""
        return self.announce_emergency(
            "Test Patient",
            "Test emergency condition check one two three",
            "101",
            "A",
            "test"
        )
    
    def shutdown(self):
        """Stop the worker thread"""
        self.is_running = False
        self.announcement_queue.put(None)

# Global announcer instance
emergency_announcer = EmergencyAnnouncer()

def announce_emergency_alert(patient_name, condition, room_number, bed_number=None, severity='critical'):
    """Convenience function to announce emergency alerts"""
    return emergency_announcer.announce_emergency(patient_name, condition, room_number, bed_number, severity)

def announce_vital_emergency(patient, vital_type, value, unit, room_number, bed_number=None):
    """Convenience function to announce vital sign emergencies"""
    return emergency_announcer.announce_vital_alert(patient, vital_type, value, unit, room_number, bed_number)

def announce_general_emergency(patient_name, condition_description, room_number, bed_number=None):
    """Convenience function to announce general emergencies"""
    return emergency_announcer.announce_emergency(patient_name, condition_description, room_number, bed_number, 'critical')
