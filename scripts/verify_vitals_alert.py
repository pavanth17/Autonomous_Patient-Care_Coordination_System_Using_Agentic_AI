import requests
import json
import time
import sys

# Target API
API_URL = 'http://192.168.101.27:5000/api/arduino/vitals'

def test_vital_alert(bpm, spo2, temp, expected_status='critical'):
    print(f"\n--- Testing Vitals: BPM={bpm}, SpO2={spo2}, Temp={temp} ---")
    payload = {
        'bpm': bpm,
        'spo2': spo2,
        'temp': temp
    }
    
    try:
        response = requests.post(API_URL, json=payload, timeout=5)
        if response.status_code == 200:
            data = response.json()
            print(f"[SUCCESS] Server responded: {data.get('message')}")
            
            # Since we can't easily check the DB from here without more setup,
            # we rely on the server logs and the dashboard.
            print(f"[INFO] Expected Status: {expected_status}")
            print(f"[ACTION] Please check the Doctor/Nurse dashboard for a {expected_status} alert.")
            print(f"[ACTION] Please check your mobile (+916309661379) for an SMS alert.")
        else:
            print(f"[ERROR] Server returned {response.status_code}: {response.text}")
    except Exception as e:
        print(f"[ERROR] Could not connect to server: {e}")

if __name__ == "__main__":
    print("CareSync AI: Vitals Alert Verification")
    
    # 1. Normal Vitals (within 60-80, 95-100, 96-99)
    test_vital_alert(70, 98, 98.0, expected_status='normal')
    
    time.sleep(2)
    
    # 2. Critical BPM (Low)
    test_vital_alert(55, 98, 98.0, expected_status='critical')
    
    time.sleep(2)
    
    # 3. Critical BPM (High)
    test_vital_alert(85, 98, 98.0, expected_status='critical')
    
    time.sleep(2)
    
    # 4. Critical SpO2 (Low)
    test_vital_alert(75, 94, 98.0, expected_status='critical')
    
    time.sleep(2)
    
    # 5. Critical Temp (High)
    test_vital_alert(75, 98, 100.2, expected_status='critical')
    
    print("\nVerification pulse complete.")
