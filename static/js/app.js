document.addEventListener('DOMContentLoaded', function() {
    initializeFlashMessages();
    document.addEventListener('submit', function(event) {
        const form = event.target;
        if (form.matches('form[action*="/acknowledge"]')) {
            stopEmergencyVoice(form.dataset.alertId || '');
        }
    });
});

function initializeFlashMessages() {
    const alerts = document.querySelectorAll('.flash-messages .alert');
    alerts.forEach(alert => {
        setTimeout(() => {
            const bsAlert = new bootstrap.Alert(alert);
            bsAlert.close();
        }, 5000);
    });
}

// Expose the emergency alert handler globally for templates that call it
if (typeof window !== 'undefined') {
    window.showEmergencyAlert = showEmergencyAlert;
    window.handleAlertAcknowledged = handleAlertAcknowledged;
}

function showEmergencyAlert(alert) {
    const modal = document.getElementById('emergencyAlertModal');
    if (!modal) return;

    stopEmergencyVoice(alert.id || '');
    
    // Store alert ID on modal for potential use
    modal.dataset.currentAlertId = alert.id || '';
    
    // Update modal content with alert details
    const titleEl = document.getElementById('alertModalTitle');
    const patientEl = document.getElementById('alertPatientName');
    const locationEl = document.getElementById('alertLocation');
    const messageEl = document.getElementById('alertMessage');
    const timerEl = document.getElementById('countdownTimer');
    
    if (titleEl) titleEl.textContent = alert.severity === 'critical' ? 'CRITICAL ALERT' : 'Emergency Alert';
    if (patientEl) patientEl.textContent = alert.patient_name || 'Unknown Patient';
    if (locationEl) locationEl.textContent = `Room ${alert.room || '-'}, Bed ${alert.bed || '-'}`;
    if (messageEl) messageEl.textContent = alert.message || 'Patient requires immediate attention';
    if (timerEl) timerEl.textContent = '60';
    
    // Initialize and show the modal
    const bsModal = new bootstrap.Modal(modal, { backdrop: 'static', keyboard: false });
    bsModal.show();
    
    // Initialize countdown
    let countdown = 60;
    const countdownInterval = setInterval(() => {
        countdown = Math.max(0, countdown - 1);
        if (timerEl) timerEl.textContent = countdown;
        if (countdown <= 0) {
            clearInterval(countdownInterval);
            try { bsModal.hide(); } catch (e) {}
        }
    }, 1000);
    
    // Wire acknowledge button
    const ackBtn = document.getElementById('acknowledgeAlertBtn');
    if (ackBtn) {
        ackBtn.onclick = function(e) {
            e.preventDefault();
            clearInterval(countdownInterval);
            if (alert.id) {
                acknowledgeAlert(alert.id).then(() => {
                    try { bsModal.hide(); } catch (e) {}
                }).catch(() => {
                    try { bsModal.hide(); } catch (e) {}
                });
            } else {
                try { bsModal.hide(); } catch (e) {}
            }
        };
    }
    
    // Clean up interval when modal closes
    const modalCloseHandler = function() {
        clearInterval(countdownInterval);
        modal.removeEventListener('hidden.bs.modal', modalCloseHandler);
    };
    modal.addEventListener('hidden.bs.modal', modalCloseHandler);
    
    // Play alert sound and voice only for authenticated doctor/nurse dashboards.
    playAlertSound(60);
    if (window.staffRole === 'doctor' || window.staffRole === 'nurse') {
        speakEmergencyAlert(alert);
    }
}

function speakEmergencyAlert(alert) {
    if (!('speechSynthesis' in window)) return;
    if (window.staffRole !== 'doctor' && window.staffRole !== 'nurse') return;

    const patientName = alert.patient_name || 'Unknown Patient';
    const room = alert.room || '-';
    const bed = alert.bed || '-';
    const message = alert.message || 'Patient requires immediate attention';

    // English Template
    const englishText = `Emergency alert for patient ${patientName} in room ${room}, bed ${bed}. ${message}.`;
    
    // Telugu Template
    const teluguText = `అత్యవసర హెచ్చరిక! గది ${room}, బెడ్ ${bed} లో ఉన్న రోగి ${patientName} కి సాయం కావాలి.`;
    
    // Tamil Template
    const tamilText = `அவசரகால எச்சரிக்கை! அறை ${room}, படுக்கை ${bed} இல் உள்ள நோயாளி ${patientName} க்கு உதவி தேவை.`;

    const utteranceEnglish = new SpeechSynthesisUtterance(englishText);
    utteranceEnglish.lang = 'en-US';
    utteranceEnglish.rate = 0.9;

    const utteranceTelugu = new SpeechSynthesisUtterance(teluguText);
    utteranceTelugu.lang = 'te-IN';
    utteranceTelugu.rate = 0.9;

    const utteranceTamil = new SpeechSynthesisUtterance(tamilText);
    utteranceTamil.lang = 'ta-IN';
    utteranceTamil.rate = 0.9;

    // Speak in sequence
    window.speechSynthesis.speak(utteranceEnglish);
    window.speechSynthesis.speak(utteranceTelugu);
    window.speechSynthesis.speak(utteranceTamil);
}

function stopEmergencyVoice(alertId = '') {
    if ('speechSynthesis' in window) {
        window.speechSynthesis.cancel();
    }
    if (window.activeAlertSoundTimers) {
        window.activeAlertSoundTimers.forEach(timer => clearTimeout(timer));
        window.activeAlertSoundTimers.clear();
    }
    if (window.activeAlertAudioContexts) {
        window.activeAlertAudioContexts.forEach(context => context.close());
        window.activeAlertAudioContexts.clear();
    }
    window.activeVoiceAlertId = alertId;
}

function handleAlertAcknowledged(alertId) {
    if (String(window.activeVoiceAlertId || '') !== String(alertId || '')) return;

    stopEmergencyVoice();
    const modal = document.getElementById('emergencyAlertModal');
    if (modal && String(modal.dataset.currentAlertId || '') === String(alertId)) {
        const modalInstance = bootstrap.Modal.getInstance(modal);
        if (modalInstance) modalInstance.hide();
    }
}

function playAlertSound(durationInSeconds = 0) {
    try {
        if (!window.activeAlertSoundTimers) window.activeAlertSoundTimers = new Set();
        if (!window.activeAlertAudioContexts) window.activeAlertAudioContexts = new Set();
        const audioContext = new (window.AudioContext || window.webkitAudioContext)();
        window.activeAlertAudioContexts.add(audioContext);
        let startTime = Date.now();
        
        function beep() {
            // Check if we should still be playing
            if (durationInSeconds > 0 && (Date.now() - startTime) >= (durationInSeconds * 1000)) {
                return;
            }

            const oscillator = audioContext.createOscillator();
            const gainNode = audioContext.createGain();
            
            oscillator.connect(gainNode);
            gainNode.connect(audioContext.destination);
            
            oscillator.frequency.value = 880;
            oscillator.type = 'sine';
            
            gainNode.gain.setValueAtTime(0.3, audioContext.currentTime);
            gainNode.gain.exponentialRampToValueAtTime(0.01, audioContext.currentTime + 0.5);
            
            oscillator.start(audioContext.currentTime);
            oscillator.stop(audioContext.currentTime + 0.5);
            
            // Loop if duration is specified
            if (durationInSeconds > 0) {
                const timer = setTimeout(() => {
                    window.activeAlertSoundTimers.delete(timer);
                    beep();
                }, 1000); // Beep every second
                window.activeAlertSoundTimers.add(timer);
            }
        }
        
        beep();
    } catch (e) {
        console.log('Audio not supported or blocked by browser policy');
    }
}

function acknowledgeAlert(alertId) {
    stopEmergencyVoice(alertId);
    // Attempt AJAX POST acknowledgement. If it fails, fall back to form submit.
    return new Promise((resolve, reject) => {
        fetch(`/alert/${alertId}/acknowledge`, {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'X-Requested-With': 'XMLHttpRequest'
            }
        }).then(resp => {
            if (resp.ok) {
                // update any alert item in the UI
                const el = document.querySelector(`.alert-item[data-alert-id="${alertId}"]`);
                if (el) {
                    el.classList.add('acknowledged');
                    // remove or mark acknowledged visually
                    el.style.opacity = '0.6';
                }
                resolve(true);
            } else {
                // fallback to form submit
                const form = document.createElement('form');
                form.method = 'POST';
                form.action = `/alert/${alertId}/acknowledge`;
                document.body.appendChild(form);
                form.submit();
                resolve(false);
            }
        }).catch(err => {
            // network error, fallback to form
            const form = document.createElement('form');
            form.method = 'POST';
            form.action = `/alert/${alertId}/acknowledge`;
            document.body.appendChild(form);
            form.submit();
            reject(err);
        });
    });
}

function showToast(message, type = 'info') {
    const container = document.getElementById('alertContainer') || document.body;
    
    const toast = document.createElement('div');
    toast.className = `alert alert-${type} alert-dismissible fade show`;
    toast.role = 'alert';
    toast.innerHTML = `
        ${message}
        <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
    `;
    
    container.appendChild(toast);
    
    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => toast.remove(), 150);
    }, 5000);
}

function formatTime(isoString) {
    const date = new Date(isoString);
    return date.toLocaleTimeString();
}

function updateVitalDisplay(patientId, vitals) {
    const card = document.querySelector(`[data-patient-id="${patientId}"]`);
    if (!card) return;
    
    const heartRate = card.querySelector('[data-vital="heart_rate"]');
    const bp = card.querySelector('[data-vital="bp"]');
    const oxygen = card.querySelector('[data-vital="oxygen"]');
    const temperature = card.querySelector('[data-vital="temperature"]');
    const timestamp = card.querySelector('[data-vital="timestamp"]');
    
    if (heartRate) {
        heartRate.textContent = Math.round(vitals.heart_rate) || '--';
        // Range 60-80 as normal
        updateVitalClass(heartRate.closest('.vital-item'), vitals.heart_rate, 55, 85, 60, 80);
    }
    
    if (bp && vitals.bp_systolic && vitals.bp_diastolic) {
        bp.textContent = `${Math.round(vitals.bp_systolic)}/${Math.round(vitals.bp_diastolic)}`;
        updateVitalClass(bp.closest('.vital-item'), vitals.bp_systolic, 90, 160, 100, 140);
    }
    
    if (oxygen) {
        oxygen.textContent = Math.round(vitals.oxygen) || '--';
        // Range 95-100 as normal
        updateVitalClass(oxygen.closest('.vital-item'), vitals.oxygen, 92, 100, 95, 100, true);
    }
    
    if (temperature) {
        temperature.textContent = vitals.temperature ? vitals.temperature.toFixed(1) : '--';
        // Range 96-99 as normal
        updateVitalClass(temperature.closest('.vital-item'), vitals.temperature, 95, 100, 96, 99);
    }
    
    if (timestamp) {
        timestamp.textContent = formatTime(vitals.recorded_at);
    }
    
    card.classList.remove('patient-normal', 'patient-warning', 'patient-critical');
    card.classList.add(`patient-${vitals.vital_status || 'normal'}`);
}

function updateVitalClass(element, value, critLow, critHigh, warnLow, warnHigh, invertLogic = false) {
    if (!element || value === null || value === undefined) return;
    
    element.classList.remove('vital-normal', 'vital-warning', 'vital-critical');
    
    if (invertLogic) {
        if (value < critLow) {
            element.classList.add('vital-critical');
        } else if (value < warnLow) {
            element.classList.add('vital-warning');
        } else {
            element.classList.add('vital-normal');
        }
    } else {
        if (value < critLow || value > critHigh) {
            element.classList.add('vital-critical');
        } else if (value < warnLow || value > warnHigh) {
            element.classList.add('vital-warning');
        } else {
            element.classList.add('vital-normal');
        }
    }
}

function showMedicineDetails(medName) {
    const modal = document.getElementById('medicineDetailModal');
    if (!modal) return;

    // Initialize or get existing modal instance
    let bsModal = bootstrap.Modal.getInstance(modal);
    if (!bsModal) {
        bsModal = new bootstrap.Modal(modal);
    }
    
    // Reset modal state to loading
    document.getElementById('medicineDetailContent').style.display = 'block';
    document.getElementById('medicineDetailActualContent').style.display = 'none';
    document.getElementById('medicineDetailContent').innerHTML = `
        <div class="text-center p-4">
            <div class="spinner-border text-primary" role="status">
                <span class="visually-hidden">Loading...</span>
            </div>
        </div>
    `;
    
    bsModal.show();

    // Fetch details from API
    fetch(`/api/medicine-details/${encodeURIComponent(medName)}`)
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                document.getElementById('medName').textContent = data.name;
                document.getElementById('medUsage').textContent = data.usage;
                document.getElementById('medDosage').textContent = data.dosage;
                document.getElementById('medPrecautions').textContent = data.precautions;
                
                document.getElementById('medicineDetailContent').style.display = 'none';
                document.getElementById('medicineDetailActualContent').style.display = 'block';
            } else {
                document.getElementById('medicineDetailContent').innerHTML = `
                    <div class="alert alert-warning m-3">
                        <i class="bi bi-exclamation-triangle me-2"></i>
                        Details for "${medName}" not found in our medical database.
                    </div>
                `;
            }
        })
        .catch(err => {
            console.error('Error fetching medicine details:', err);
            document.getElementById('medicineDetailContent').innerHTML = `
                <div class="alert alert-danger m-3">
                    <i class="bi bi-x-circle me-2"></i>
                    Error loading medicine details. Please try again later.
                </div>
            `;
        });
}
