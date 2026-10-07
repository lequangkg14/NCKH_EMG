/*
===========================================================
 ESP32-S3 EMG Data Acquisition Firmware
 Version : 1.4  (5 kenh)
 Purpose : Ghi dataset tu 5 cam bien Muscle Sensor v3
           (dau ra la DUONG BAO da chinh luu + lam muot ~2 Hz)

 Bo loc giu NGUYEN nhu v1.3 (triet nhieu luoi dien 50 Hz va gon song 100 Hz):
   - Lay mau ADC nhanh 1 kHz (moi kenh 1 mau / 1 ms)
   - Trung binh truot 20 ms (20 mau) cho moi kenh.
     20 ms la boi so chu ky 50 Hz (20 ms) va 100 Hz (10 ms),
     nen bo loc nay triet 50, 100, 150 Hz ... (cac "null" cua boxcar)
   - Xuat ra 100 Hz (moi 10 ms 1 dong) -> OUT_RATE = 100 Hz
     (cac dong lien tiep chong lan 50 %, do tre nhom ~10 ms)

 Doi so voi v1.3: tu 6 kenh -> 5 kenh (cam bien kenh 1 cu bi hong).
 Giao thuc:  SEQ,TIME_US,CH1,CH2,CH3,CH4,CH5   (7 cot)
   CH1..CH5 = GPIO 2,4,5,6,7  (= CH2..CH6 cu cua ban 6 kenh)
   TIME_US = micros() tai mau moi nhat cua cua so trung binh
 Don vi: ADC count 0..4095 (hoac mV neu USE_MILLIVOLTS = 1)

 Luu y Arduino IDE (ESP32-S3 N16R8):
   - Tools > USB CDC On Boot: Enabled neu dung cong USB (native)
   - Tools > PSRAM: OPI PSRAM
===========================================================
*/

#define NUM_CHANNELS 5

// ======= Chan ADC1. S3: GPIO1..10 (tranh GPIO3). ESP32 thuong: GPIO32..39 =======
// Chan ADC1 theo loai board (tu nhan biet luc bien dich)
#if defined(CONFIG_IDF_TARGET_ESP32S3)
  const uint8_t EMG_PIN[NUM_CHANNELS] = {2, 4, 5, 6, 7};        // ESP32-S3: ADC1 (BOARD CHINH)
  #define BOARD_NAME "ESP32-S3"
#elif defined(CONFIG_IDF_TARGET_ESP32)
  const uint8_t EMG_PIN[NUM_CHANNELS] = {34, 35, 32, 33, 25};   // ESP32 DevKit V1: ADC1 (CHI DE TEST)
  #define BOARD_NAME "ESP32 DevKit V1 (CHI DE TEST)"
#else
  #error "Board chua ho tro: chi ESP32-S3 va ESP32 (DevKit V1)"
#endif

// ======= Cau hinh =======
const uint32_t ACQ_RATE  = 1000;    // Hz - toc do lay mau ADC noi bo
const uint16_t WINDOW_N  = 20;      // so mau trong cua so trung binh (20 ms @ 1 kHz)
const uint16_t OUT_DIV   = 10;      // xuat 1 dong moi OUT_DIV mau -> 1000/10 = 100 Hz
#define USE_MILLIVOLTS 0            // 0: ADC count (0..4095), 1: mV da hieu chuan
#define DISCARD_FIRST_READ 0        // 1: doc bo 1 mau sau khi chuyen kenh (cham hon ~2x)
#define PRINT_OVERRUN_INFO 0        // 1: in "# overruns=..." moi 10 s

const uint32_t ACQ_INTERVAL_US = 1000000UL / ACQ_RATE;

// ======= Trang thai =======
uint16_t ring[NUM_CHANNELS][WINDOW_N];   // bo dem vong, 20 mau gan nhat moi kenh
uint32_t sum[NUM_CHANNELS];              // tong cua so truot
uint16_t head = 0;
uint16_t filled = 0;                     // so mau da co trong cua so (toi da WINDOW_N)
uint16_t sinceEmit = 0;

uint32_t sequence = 0;
uint32_t nextTick;
uint32_t overrunCount = 0;
uint32_t lastInfoMs = 0;

char lineBuf[128];

static inline uint16_t readOnce(uint8_t pin)
{
#if DISCARD_FIRST_READ
  (void)analogRead(pin);
#endif
#if USE_MILLIVOLTS
  return (uint16_t)analogReadMilliVolts(pin);
#else
  return (uint16_t)analogRead(pin);
#endif
}

void setup()
{
  Serial.begin(921600);   // voi USB CDC native, toc do baud bi bo qua
  analogReadResolution(12);
  for (int i = 0; i < NUM_CHANNELS; i++)
  {
    pinMode(EMG_PIN[i], INPUT);
    analogSetPinAttenuation(EMG_PIN[i], ADC_11db);   // ~0..3.1 V
    sum[i] = 0;
    for (int k = 0; k < WINDOW_N; k++) ring[i][k] = 0;
  }

  // Khoi dong ADC vai vong cho on dinh
  for (int w = 0; w < 50; w++)
  {
    for (int i = 0; i < NUM_CHANNELS; i++) analogRead(EMG_PIN[i]);
  }

  // Tu do thoi gian doc de biet 1 kHz co kip khong
  uint32_t t0 = micros();
  for (int k = 0; k < 600; k++) (void)readOnce(EMG_PIN[k % NUM_CHANNELS]);
  float avgRead = (micros() - t0) / 600.0f;
  float perTick = avgRead * NUM_CHANNELS;

  delay(200);
  Serial.println("# ESP32-S3 EMG DAQ READY v1.4");
  Serial.print("# Board="); Serial.println(BOARD_NAME);
  Serial.print("# AcqRate_Hz=");  Serial.print(ACQ_RATE);
  Serial.print(" Window_ms=");    Serial.print(WINDOW_N * 1000UL / ACQ_RATE);
  Serial.print(" OutRate_Hz=");   Serial.println(ACQ_RATE / OUT_DIV);
  Serial.print("# avg_read_us="); Serial.print(avgRead, 1);
  Serial.print(" per_tick_us=");  Serial.print(perTick, 0);
  Serial.print(" tick_interval_us="); Serial.println(ACQ_INTERVAL_US);
  if (perTick > 0.8f * ACQ_INTERVAL_US)
    Serial.println("# WARNING: doc 5 kenh qua sat chu ky - giam ACQ_RATE hoac tat DISCARD_FIRST_READ");
  Serial.println("# Format: SEQ,TIME_US,CH1,CH2,CH3,CH4,CH5");

  nextTick = micros() + ACQ_INTERVAL_US;
}

void loop()
{
  uint32_t now = micros();
  int32_t late = (int32_t)(now - nextTick);
  if (late < 0) return;                       // chua den gio

  // Tre hon 1 chu ky -> dem overrun va dong bo lai (khong doc don)
  if (late >= (int32_t)ACQ_INTERVAL_US)
  {
    overrunCount++;
    nextTick = now + ACQ_INTERVAL_US;
  }
  else
  {
    nextTick += ACQ_INTERVAL_US;
  }

  uint32_t timestamp = now;

  // Doc 5 kenh va cap nhat tong truot
  for (int i = 0; i < NUM_CHANNELS; i++)
  {
    uint16_t v = readOnce(EMG_PIN[i]);
    sum[i] += v;
    sum[i] -= ring[i][head];
    ring[i][head] = v;
  }
  head++;
  if (head >= WINDOW_N) head = 0;
  if (filled < WINDOW_N) filled++;
  sinceEmit++;

  // Xuat ra khi cua so da day va du OUT_DIV mau ke tu lan xuat truoc
  if (filled == WINDOW_N && sinceEmit >= OUT_DIV)
  {
    sinceEmit = 0;
    int out[NUM_CHANNELS];
    for (int i = 0; i < NUM_CHANNELS; i++)
      out[i] = (int)((sum[i] + WINDOW_N / 2) / WINDOW_N);

    int n = snprintf(lineBuf, sizeof(lineBuf), "%lu,%lu",
                     (unsigned long)sequence, (unsigned long)timestamp);
    for (int i = 0; i < NUM_CHANNELS && n > 0 && n < (int)sizeof(lineBuf); i++)
      n += snprintf(lineBuf + n, sizeof(lineBuf) - n, ",%d", out[i]);
    if (n > 0)
    {
      Serial.write((const uint8_t*)lineBuf, n);
      Serial.write('\n');
    }
    sequence++;
  }

#if PRINT_OVERRUN_INFO
  if (millis() - lastInfoMs >= 10000)
  {
    lastInfoMs = millis();
    Serial.print("# overruns=");
    Serial.println(overrunCount);
  }
#endif
}
