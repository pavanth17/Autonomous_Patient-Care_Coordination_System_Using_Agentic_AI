import serial
import requests
import json
import time
import re

# Configuration
SERIAL_PORT = 'COM7' 
BAUD_RATE = 115200
API_URLS = [
    'http://127.0.0.1:5000/api/arduino/vitals',
    'http://192.168.101.27:5000/api/arduino/vitals'
]

print(f"--- CareSync AI Bridge: High-Availability Mode ---")
print(f"Connecting to Arduino on {SERIAL_PORT}...")

try:
    ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0.1)
    time.sleep(2) 
    print("STATUS: Connected! Tracking all Serial activity...")
    
    # Flush any stale data in the buffer
    ser.reset_input_buffer()
    
    while True:
        if ser.in_waiting > 0:
            try:
                # If buffer is building up, skip to the latest data
                if ser.in_waiting > 100:
                    ser.reset_input_buffer()
                    time.sleep(0.1)
                    continue
                
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                if not line: continue
                
                print(f"SERIAL RECORD -> {line}")
                
                # Look for vitals pattern
                match = re.search(r'BPM:(\d+),SPO2:(\d+),TEMP:([\d.]+)', line)
                if match:
                    payload = {
                        'bpm': int(match.group(1)),
                        'spo2': int(match.group(2)),
                        'temp': float(match.group(3))
                    }
                    
                    print(f"ACTION: Attempting to push vitals to Dashboards...")
                    success = False
                    for url in API_URLS:
                        try:
                            # 2 second timeout for faster response
                            response = requests.post(url, json=payload, timeout=2)
                            if response.status_code == 200:
                                print(f"PUSH SUCCESS: {url} | Data: {payload}")
                                success = True
                                break
                            else:
                                print(f"PUSH FAILED: {url} returned {response.status_code}")
                        except Exception:
                            # Suppress individual URL errors to keep log clean
                            continue
                    
                    if not success:
                        print("CRITICAL: All server endpoints unreachable! Is the CareSync app running?")
                    else:
                        # 1-second delay for every vital value (as requested)
                        # This avoids overwhelming the UI/Dashboard
                        time.sleep(1.0)
                        # Clear buffer again to stay real-time after the sleep
                        ser.reset_input_buffer()
                        
            except Exception as e:
                print(f"DATA ERROR: {e}")
                    
        time.sleep(0.01)

except KeyboardInterrupt:
    print("\nCareSync Bridge stopped by user.")
except Exception as e:
    print(f"SERIAL ERROR: {e}")
finally:
    if 'ser' in locals() and ser.is_open:
        ser.close()
