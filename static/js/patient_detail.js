let vitalsChart = null;

document.addEventListener('DOMContentLoaded', function() {
    initializeVitalsChart();
    startPatientVitalsPolling();
});

function initializeVitalsChart() {
    const ctx = document.getElementById('vitalsChart');
    if (!ctx) return;
    
    const chartData = prepareChartData(vitalsHistory);
    
    vitalsChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: chartData.labels,
            datasets: [
                {
                    label: 'Heart Rate (bpm)',
                    data: chartData.heartRate,
                    borderColor: '#dc3545',
                    backgroundColor: 'rgba(220, 53, 69, 0.1)',
                    tension: 0.4,
                    fill: false
                },
                {
                    label: 'SpO2 (%)',
                    data: chartData.oxygen,
                    borderColor: '#0dcaf0',
                    backgroundColor: 'rgba(13, 202, 240, 0.1)',
                    tension: 0.4,
                    fill: false
                },
                {
                    label: 'Temperature (°F)',
                    data: chartData.temperature,
                    borderColor: '#ffc107',
                    backgroundColor: 'rgba(255, 193, 7, 0.1)',
                    tension: 0.4,
                    fill: false,
                    yAxisID: 'y1'
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: {
                mode: 'index',
                intersect: false
            },
            plugins: {
                legend: {
                    position: 'top',
                },
                tooltip: {
                    callbacks: {
                        title: function(context) {
                            return new Date(context[0].label).toLocaleString();
                        }
                    }
                }
            },
            scales: {
                x: {
                    display: true,
                    title: {
                        display: true,
                        text: 'Time'
                    },
                    ticks: {
                        callback: function(value, index) {
                            const date = new Date(this.getLabelForValue(value));
                            return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
                        },
                        maxTicksLimit: 10
                    }
                },
                y: {
                    type: 'linear',
                    display: true,
                    position: 'left',
                    title: {
                        display: true,
                        text: 'Heart Rate / SpO2'
                    },
                    min: 0,
                    max: 150
                },
                y1: {
                    type: 'linear',
                    display: true,
                    position: 'right',
                    title: {
                        display: true,
                        text: 'Temperature (°F)'
                    },
                    min: 95,
                    max: 105,
                    grid: {
                        drawOnChartArea: false
                    }
                }
            }
        }
    });
}

function prepareChartData(vitals) {
    const reversed = [...vitals].reverse();
    
    return {
        labels: reversed.map(v => v.recorded_at),
        heartRate: reversed.map(v => v.heart_rate),
        oxygen: reversed.map(v => v.oxygen_saturation || v.oxygen),
        temperature: reversed.map(v => v.temperature)
    };
}

function startPatientVitalsPolling() {
    if (typeof patientId === 'undefined') return;
    
    setInterval(() => {
        fetchPatientVitals(patientId);
    }, 5000);
}

function fetchPatientVitals(patientId) {
    fetch(`/api/patient/${patientId}/vitals`)
        .then(response => response.json())
        .then(vitals => {
            if (vitals.length > 0) {
                updatePatientDetailVitals(vitals[0]);
                updateChart(vitals);
            }
        })
        .catch(error => console.error('Error fetching vitals:', error));
}

function updatePatientDetailVitals(vital) {
    const heartRate = document.getElementById('heartRate');
    const bloodPressure = document.getElementById('bloodPressure');
    const oxygenSat = document.getElementById('oxygenSat');
    const temperature = document.getElementById('temperature');
    const respRate = document.getElementById('respRate');
    const lastUpdated = document.getElementById('lastUpdated');
    
    if (heartRate) {
        heartRate.textContent = Math.round(vital.heart_rate) || 'Nill';
        updateVitalCardClass(heartRate.closest('.vital-card'), vital.heart_rate, 50, 130, 60, 100);
    }
    
    if (bloodPressure) {
        bloodPressure.textContent = (vital.bp_systolic !== null && vital.bp_diastolic !== null) ? `${vital.bp_systolic}/${vital.bp_diastolic}` : 'Nill/Nill';
        updateVitalCardClass(bloodPressure.closest('.vital-card'), vital.bp_systolic, 90, 160, 100, 140);
    }
    
    if (oxygenSat) {
        oxygenSat.textContent = Math.round(vital.oxygen) || 'Nill';
        updateVitalCardClass(oxygenSat.closest('.vital-card'), vital.oxygen, 90, 100, 95, 100, true);
    }
    
    if (temperature) {
        temperature.textContent = vital.temperature ? vital.temperature.toFixed(1) : 'Nill';
        updateVitalCardClass(temperature.closest('.vital-card'), vital.temperature, 96, 102, 97, 100);
    }
    
    if (respRate) {
        respRate.textContent = vital.respiratory_rate || 'Nill';
    }
    
    if (lastUpdated) {
        lastUpdated.textContent = new Date(vital.recorded_at).toLocaleString();
    }
}

function updateVitalCardClass(element, value, critLow, critHigh, warnLow, warnHigh, invertLogic = false) {
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

function updateChart(vitals) {
    if (!vitalsChart) return;
    
    const chartData = prepareChartData(vitals);
    
    vitalsChart.data.labels = chartData.labels;
    vitalsChart.data.datasets[0].data = chartData.heartRate;
    vitalsChart.data.datasets[1].data = chartData.oxygen;
    vitalsChart.data.datasets[2].data = chartData.temperature;
    
    vitalsChart.update('none');
}
