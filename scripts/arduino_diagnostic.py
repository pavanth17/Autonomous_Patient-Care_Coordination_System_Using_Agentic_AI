import serial
import serial.tools.list_ports
import requests
import json
import time
import re

# Configuration
PORT = 'COM7'
BAUD = 115200
API_URL = 'http://127.0.0.1:5000/api/arduino/vitals'
PATTERN = r'BPM:(\d+),SPO2:(\d+),TEMP:([\d.]+)'

print("=== CareSync Arduino Diagnostic Tool ===")

# 1. List Available Ports
ports = list(serial.tools.list_ports.comports())
print(f"\n1. Scanning for COM ports...")
if not ports:
    print("   [ERROR] No COM ports found! Is the Arduino plugged in?")
else:
    for p in ports:
        print(f"   - Found: {p}")

# 2. Test API Connectivity
print(f"\n2. Testing connection to Server at {API_URL}...")
try:
    test_payload = {'bpm': 0, 'spo2': 0, 'temp': 0}
    res = requests.post(API_URL, json=test_payload, timeout=5)
    if res.status_code == 200:
        print(f"   [SUCCESS] Server responded: {res.text}")
    else:
        print(f"   [ERROR] Server returned {res.status_code}: {res.text}")
except Exception as e:
    print(f"   [ERROR] Could not reach server: {e}")

# 3. Test Serial Reading
print(f"\n3. Attempting to read from {PORT} at {BAUD} baud...")
try:
    ser = serial.Serial(PORT, BAUD, timeout=1)
    print(f"   [SUCCESS] Opened {PORT}. Waiting for data (10 seconds)...")
    
    start_time = time.time()
    while time.time() - start_time < 10:
        if ser.in_waiting > 0:
            line = ser.readline().decode('utf-8', errors='ignore').strip()
            print(f"   [RAW DATA] {line}")
            
            match = re.search(PATTERN, line)
            if match:
                bpm, spo2, temp = match.groups()
                print(f"   [MATCHED] BPM: {bpm}, SPO2: {spo2}, TEMP: {temp}")
                print("   [SUCCESS] Regex pattern matches perfectly!")
                ser.close()
                break
    else:
        print("   [ERROR] No matching data received in 10 seconds.")
    ser.close()
except Exception as e:
    print(f"   [ERROR] Serial error: {e}")

print("\n=== Diagnostic Complete ===")
