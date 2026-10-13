/*
  emg_ei_inference_v3.ino
  EMG 4 kênh (GPIO 1,2,4,5) -> Edge Impulse NCKH_EMG_2 (100 Hz, cửa sổ 500 ms)
  -> PCA9685 -> 5 servo        Board: ESP32-S3

  Tiền xử lý GIỐNG logger v1.4 (đúng như lúc thu data):
   - ADC 1 kHz, trung bình trượt 20 ms mỗi kênh, xuất 100 Hz (mỗi 10 ms 1 mẫu)

  Kiến trúc:
   - Core 0: samplingTask đọc ADC 1 kHz, lọc, ghi mẫu 100 Hz vào ring buffer
   - Core 1: loop() cứ 100 ms copy 1 cửa sổ, chạy run_classifier, vote 3/5, điều khiển servo

  !!! Kiểm tra các chỗ CẢNH BÁO / TODO trước khi nạp !!!
*/

#include <NCKH_EMG_2_inferencing.h>   // đúng tên header của thư viện v1.0.3 (đã kiểm tra)
#include <Wire.h>
#include <Adafruit_PWMServoDriver.h>

// ================== CẤU HÌNH ==================
#define ENABLE_SERVO 1               // 0 = chỉ in Serial để test model, 1 = bật servo

// CH1..CH4 = GPIO 1,2,4,5 (đều là ADC1 của ESP32-S3).
// CẢNH BÁO: thứ tự này PHẢI trùng với thứ tự kênh trong file logger lúc thu data để train model.
const uint8_t EMG_PINS[4] = {1, 2, 4, 5};

// CẢNH BÁO I2C: ESP32-S3 KHÔNG có GPIO22. Mặc định dùng SDA=8, SCL=9 (hợp lệ, không trùng EMG).
// Phải đấu dây PCA9685 vào đúng 2 chân này. Nếu board/dây của bạn đang dùng chân khác thì đổi ở đây.
const int PCA_SDA = 8;
const int PCA_SCL = 9;
const uint8_t PCA_ADDR = 0x40;

const int INFER_STRIDE_MS = 100;     // giống Window increase trong Edge Impulse
const float CONF_MIN = 0.60f;        // dưới ngưỡng này coi như "không chắc"
const int VOTE_N = 5;                // vote 3/5 kết quả gần nhất
const int VOTE_NEED = 3;

// Servo: lấy nguyên từ 5finger_with_text.ino đã test (kênh PCA9685, tick xung, góc)
// Thứ tự phần tử 0..4 PHẢI trùng thứ tự ngón trong bảng POSES: {cái, trỏ, giữa, áp út, út}
// TODO: xác nhận phím 1..5 trong file test (CH0,2,4,6,8) đúng là cái..út theo thứ tự này
#define SERVO_FREQ 50
#define SERVOMIN   102
#define SERVOMAX   512
const uint8_t SERVO_CH[5]  = {0, 2, 4, 6, 8};
const int HOME_ANGLE[5]    = {140, 150, 40, 160, 50};   // góc ban đầu  -> coi là DUỖI
const int ACTIVE_ANGLE[5]  = {50, 10, 180, 20, 180};    // góc khi nhấn -> coi là GẬP

// Bảng tư thế: 1 = gập, 0 = duỗi. Thứ tự: cái, trỏ, giữa, áp út, út
// Model NCKH_EMG_2 chỉ có 6 nhãn (không có hand_open, three).
// TODO: sửa "hi" cho đúng định nghĩa cử chỉ của nhóm
struct Pose { const char* label; uint8_t f[5]; };
const Pose POSES[] = {
  {"rest",            {0, 0, 0, 0, 0}},
  {"Power_grip",      {1, 1, 1, 1, 1}},
  {"pinch",           {1, 1, 0, 0, 0}},
  {"point",           {1, 0, 1, 1, 1}},
  {"thumb_extension", {0, 1, 1, 1, 1}},
  {"hi",              {1, 0, 0, 1, 1}},   // giả định: trỏ+giữa duỗi
};
const int N_POSES = sizeof(POSES) / sizeof(POSES[0]);

// ================== BIẾN TOÀN CỤC ==================
constexpr int AXES = EI_CLASSIFIER_RAW_SAMPLES_PER_FRAME;  // = 4 (đã kiểm tra trong model_metadata.h)
constexpr int N_SAMPLES = EI_CLASSIFIER_RAW_SAMPLE_COUNT;  // = 50 mẫu (500 ms @100 Hz)

// Bộ lọc giống logger v1.4
constexpr uint16_t WINDOW_N = 20;   // trung bình trượt 20 mẫu @1 kHz = 20 ms
constexpr uint16_t OUT_DIV  = 10;   // 1000 Hz / 10 = 100 Hz

static_assert(EI_CLASSIFIER_FREQUENCY == 100, "Model phai chay 100 Hz");
static_assert(AXES == 4, "Model phai co dung 4 truc CH1..CH4");

static float ring[N_SAMPLES][AXES];
static float features[N_SAMPLES * AXES];
static volatile uint32_t headIdx = 0;      // vị trí sẽ ghi tiếp theo = mẫu cũ nhất
static volatile uint32_t filled = 0;
static portMUX_TYPE mux = portMUX_INITIALIZER_UNLOCKED;

static int votes[VOTE_N];
static int voteIdx = 0;
static int currentPose = -1;

Adafruit_PWMServoDriver pwm = Adafruit_PWMServoDriver(PCA_ADDR);

// ================== LẤY MẪU 1 kHz + LỌC + XUẤT 100 Hz (core 0) ==================
void samplingTask(void*) {
  static uint16_t win[AXES][WINDOW_N];   // 20 mẫu gần nhất mỗi kênh
  static uint32_t sum[AXES] = {0, 0, 0, 0};
  uint16_t wHead = 0, wFilled = 0, sinceEmit = 0;
  float s[AXES];
  TickType_t last = xTaskGetTickCount();
  for (;;) {
    vTaskDelayUntil(&last, pdMS_TO_TICKS(1));          // 1 kHz (tick FreeRTOS Arduino-ESP32 = 1 ms)
    for (int i = 0; i < AXES; i++) {
      uint16_t v = (uint16_t)analogRead(EMG_PINS[i]);
      sum[i] += v;
      sum[i] -= win[i][wHead];
      win[i][wHead] = v;
    }
    wHead = (wHead + 1) % WINDOW_N;
    if (wFilled < WINDOW_N) wFilled++;
    sinceEmit++;

    if (wFilled == WINDOW_N && sinceEmit >= OUT_DIV) {   // mỗi 10 ms ra 1 mẫu 100 Hz
      sinceEmit = 0;
      for (int i = 0; i < AXES; i++)
        s[i] = (float)((sum[i] + WINDOW_N / 2) / WINDOW_N);   // làm tròn như logger
      portENTER_CRITICAL(&mux);
      memcpy(ring[headIdx], s, sizeof(s));
      headIdx = (headIdx + 1) % N_SAMPLES;
      if (filled < N_SAMPLES) filled++;
      portEXIT_CRITICAL(&mux);
    }
  }
}

// ================== SERVO ==================
void setFinger(int i, bool flexed) {
#if ENABLE_SERVO
  int angle = constrain(flexed ? ACTIVE_ANGLE[i] : HOME_ANGLE[i], 0, 180);
  uint16_t pulse = map(angle, 0, 180, SERVOMIN, SERVOMAX);
  pwm.setPWM(SERVO_CH[i], 0, pulse);
#endif
}

void applyPose(int p) {
  if (p < 0 || p == currentPose) return;
  currentPose = p;
  for (int i = 0; i < 5; i++) setFinger(i, POSES[p].f[i]);
  Serial.printf(">>> POSE: %s\n", POSES[p].label);
}

int poseIndexOf(const char* label) {
  for (int i = 0; i < N_POSES; i++)
    if (strcmp(label, POSES[i].label) == 0) return i;
  return -1;
}

// ================== SETUP / LOOP ==================
void setup() {
  Serial.begin(115200);
  delay(1500);                           // chờ cổng Serial/USB sẵn sàng
  Serial.println("setup bat dau");

  analogReadResolution(12);
  analogSetAttenuation(ADC_11db);        // phải giống firmware thu data
  for (int r = 0; r < 50; r++)           // warm-up ADC giống logger
    for (int i = 0; i < AXES; i++) analogRead(EMG_PINS[i]);

#if ENABLE_SERVO
  Wire.begin(PCA_SDA, PCA_SCL);
  Wire.beginTransmission(PCA_ADDR);
  if (Wire.endTransmission() == 0) {
    Serial.println("PCA9685 OK");
  } else {
    Serial.printf("KHONG THAY PCA9685 (SDA=%d, SCL=%d) - kiem tra day va chan I2C\n", PCA_SDA, PCA_SCL);
  }
  pwm.begin();
  pwm.setPWMFreq(SERVO_FREQ);
  delay(10);
  for (int i = 0; i < 5; i++) setFinger(i, false);       // bắt đầu ở tư thế duỗi
#endif

  for (int i = 0; i < VOTE_N; i++) votes[i] = -1;

  Serial.printf("Model: %d mau x %d truc, %d Hz, %d nhan\n",
                N_SAMPLES, AXES, (int)EI_CLASSIFIER_FREQUENCY, (int)EI_CLASSIFIER_LABEL_COUNT);
  xTaskCreatePinnedToCore(samplingTask, "sampling", 4096, nullptr, 3, nullptr, 0);
}

void loop() {
  static uint32_t lastInfer = 0;
  if (millis() - lastInfer < INFER_STRIDE_MS) return;
  lastInfer = millis();
  if (filled < N_SAMPLES) return;        // chờ đủ 1 cửa sổ

  // Copy cửa sổ theo thứ tự cũ -> mới
  portENTER_CRITICAL(&mux);
  uint32_t h = headIdx;
  for (int i = 0; i < N_SAMPLES; i++)
    memcpy(&features[i * AXES], ring[(h + i) % N_SAMPLES], AXES * sizeof(float));
  portEXIT_CRITICAL(&mux);

  signal_t signal;
  numpy::signal_from_buffer(features, N_SAMPLES * AXES, &signal);
  ei_impulse_result_t result;
  if (run_classifier(&signal, &result, false) != EI_IMPULSE_OK) {
    Serial.println("run_classifier loi");
    return;
  }

  // Lớp có xác suất cao nhất
  int best = 0;
  for (size_t i = 1; i < EI_CLASSIFIER_LABEL_COUNT; i++)
    if (result.classification[i].value > result.classification[best].value) best = i;

  int pose = (result.classification[best].value >= CONF_MIN)
               ? poseIndexOf(result.classification[best].label) : -1;
  votes[voteIdx] = pose;
  voteIdx = (voteIdx + 1) % VOTE_N;

  // Vote đa số 3/5
  int winner = -1;
  for (int c = 0; c < N_POSES; c++) {
    int cnt = 0;
    for (int k = 0; k < VOTE_N; k++) if (votes[k] == c) cnt++;
    if (cnt >= VOTE_NEED) { winner = c; break; }
  }

  Serial.printf("pred=%s (%.2f) | dsp=%dms cls=%dms\n",
                result.classification[best].label, result.classification[best].value,
                (int)result.timing.dsp, (int)result.timing.classification);

  applyPose(winner);                     // winner = -1 thì giữ nguyên tư thế cũ
}
