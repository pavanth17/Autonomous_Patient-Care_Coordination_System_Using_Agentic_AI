#include <Wire.h>
#include "MAX30105.h"
#include <Adafruit_MLX90614.h>
#include <SSD1306Ascii.h>
#include <SSD1306AsciiWire.h>

#define OLED_I2C_ADDRESS 0x3C
SSD1306AsciiWire display;
MAX30105 particleSensor;
Adafruit_MLX90614 mlx = Adafruit_MLX90614();

// Vitals
int bpm = 0;
int spo2 = 0;
float temp = 0.0;
bool fingerDetected = false;
unsigned long lastUpdate = 0;

void setup() {
  Serial.begin(115200);
  Wire.begin();
  Wire.setClock(400000L);

  display.begin(&Adafruit128x64, OLED_I2C_ADDRESS);
  display.setFont(System5x7);
  display.clear();
  display.println(F("CareSync-AI"));

  if (!mlx.begin()) {
    display.println(F("MLX90614 Fail"));
  } else {
    display.println(F("MLX90614 Ok"));
  }

  if (!particleSensor.begin(Wire, I2C_SPEED_FAST)) {
    display.println(F("MAX30105 Fail")); 
  } else {
    particleSensor.setup(); 
    display.println(F("MAX30105 Ok"));
  }

  delay(2000);
  display.clear();
  updateOLED(); 
}

void loop() {
  long irValue = particleSensor.getIR();

  // DEBUG MODE: Always send data every 1 second to verify connection
  if (millis() - lastUpdate > 1000) {
    lastUpdate = millis();
    
    // Update Finger Status for OLED display only
    fingerDetected = (irValue > 20000);
    
    // Random Vitals for Debugging
    bpm = constrain(bpm + random(-2, 3), 40, 90);
    spo2 = constrain(98 + random(-1, 2), 90, 100);
    temp = constrain(temp + (random(-2, 3) / 10.0), 90.0, 99.0);
    if (bpm < 40) bpm = 50;
    if (temp < 90) temp = 97.5;

    updateOLED();
    sendToDashboards(); // Always send to verify bridge/dashboard link
    
    if (fingerDetected) {
       Serial.println(F("DEBUG: Finger Detected"));
    } else {
       Serial.println(F("DEBUG: No Finger (Always Sending anyway)"));
    }
  }
}

void updateOLED() {
  display.setCursor(0, 0);
  display.set2X();
  display.println(F("CareSync AI"));
  display.set1X();
  display.println(F("Patient: PAVANTH"));
  display.println(F("---------------------"));
  
  display.setCursor(0, 4);
  display.print(F("BPM:  ")); 
  if (bpm > 0) display.print(bpm); else display.print(F("--"));
  display.println(F("      "));
  
  display.setCursor(0, 6);
  display.print(F("SpO2: ")); 
  if (spo2 > 0) { display.print(spo2); display.print(F(" %")); } else display.print(F("-- %"));
  display.println(F("      "));
  
  display.setCursor(0, 7);
  display.print(F("Temp: ")); 
  if (temp > 0.1) { display.print(temp, 1); display.print(F(" F")); } else display.print(F("-- F"));
  display.println(F("      "));
}

void sendToDashboards() {
  // Serial format expected by arduino_bridge.py
  Serial.print(F("BPM:")); Serial.print(bpm);
  Serial.print(F(",SPO2:")); Serial.print(spo2);
  Serial.print(F(",TEMP:")); Serial.println(temp, 1);
}
