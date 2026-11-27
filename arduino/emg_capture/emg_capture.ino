// EMG + mouse recorder. Arduino UNO R4 Minima, shield A0, optional EXG Pill A2.
// Upload this sketch once; the Python application selects 1 or 2 channels.
#include <Arduino.h>

constexpr uint32_t BAUD = 500000;
constexpr uint32_t PERIOD_US = 500;  // 2000 samples/s PER CHANNEL
constexpr uint8_t PIN_CH1 = A0;
constexpr uint8_t PIN_CH2 = A2;
#if defined(ARDUINO_ARCH_RENESAS_UNO)
constexpr uint8_t ADC_BITS = 14;
#else
#error "Select Arduino UNO R4 Minima (or UNO R4 WiFi) in the Arduino IDE."
#endif

uint8_t channels = 1;
bool streaming = false;
uint32_t nextSample = 0, sequence = 0;

void put16(uint8_t *p, uint16_t x) {
  p[0] = uint8_t(x); p[1] = uint8_t(x >> 8);
}
void put32(uint8_t *p, uint32_t x) {
  for (uint8_t i = 0; i < 4; ++i) p[i] = uint8_t(x >> (8 * i));
}
uint16_t crc16(const uint8_t *p, uint8_t length) {
  uint16_t crc = 0xFFFF;
  while (length--) {
    crc ^= uint16_t(*p++) << 8;
    for (uint8_t i = 0; i < 8; ++i)
      crc = (crc & 0x8000) ? uint16_t((crc << 1) ^ 0x1021) : uint16_t(crc << 1);
  }
  return crc;
}
void setup() {
  analogReadResolution(ADC_BITS);
  Serial.begin(BAUD);
  pinMode(PIN_CH1, INPUT);
  pinMode(PIN_CH2, INPUT);
}
void loop() {
  while (Serial.available()) {
    char command = Serial.read();
    if (command == 'S') streaming = false;
    if (command == '1' || command == '2') {
      channels = command - '0';
      sequence = 0;
      nextSample = micros() + PERIOD_US;
      streaming = true;
    }
  }
  if (!streaming) return;
  uint32_t now = micros();
  if (int32_t(now - nextSample) < 0) return;
  // Missed deadlines remain visible as sequence gaps, never fabricated samples.
  uint32_t skipped = (now - nextSample) / PERIOD_US;
  sequence += skipped;
  nextSample += (skipped + 1) * PERIOD_US;

  analogRead(PIN_CH1); // settle the ADC after switching channels
  uint32_t t1 = micros();
  uint16_t ch1 = analogRead(PIN_CH1);
  uint16_t ch2 = 0xFFFF, delta = 0;
  if (channels == 2) {
    analogRead(PIN_CH2);
    uint32_t t2 = micros();
    ch2 = analogRead(PIN_CH2);
    delta = uint16_t(t2 - t1);
  }
  // Fixed 20-byte little-endian frame, CRC16-CCITT over bytes 0..17.
  uint8_t packet[20] = {0xA5, 0x5A};
  put32(packet + 2, sequence++);
  put32(packet + 6, t1);
  put16(packet + 10, ch1);
  put16(packet + 12, ch2);
  put16(packet + 14, delta);
  packet[16] = (ADC_BITS << 2) | channels;
  packet[17] = 1;
  put16(packet + 18, crc16(packet, 18));
  // Do not block sampling when the PC cannot drain the USB buffer.
  if (Serial.availableForWrite() >= int(sizeof(packet)))
    Serial.write(packet, sizeof(packet));
}
