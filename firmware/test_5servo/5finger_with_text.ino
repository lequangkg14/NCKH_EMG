#include <Wire.h>
#include <Adafruit_PWMServoDriver.h>

Adafruit_PWMServoDriver pwm = Adafruit_PWMServoDriver(0x40);

#define SDA_PIN     21
#define SCL_PIN     22
#define SERVO_FREQ  50

#define SERVOMIN    102
#define SERVOMAX    512

// Kênh servo
const uint8_t servoChannel[5] = {0, 2, 4, 6, 8};

// Góc ban đầu
const int homeAngle[5] = {
  140,   // CH0
  150,   // CH2
  40,    // CH4
  160,   // CH6
  50     // CH8
};

// Góc khi nhấn
const int activeAngle[5] = {
  50,    // CH0
  10,    // CH2
  180,   // CH4
  20,    // CH6
  180    // CH8
};

// Trạng thái từng servo
bool servoState[5] = {false, false, false, false, false};
// false = đang ở Home
// true  = đang ở Active

//--------------------------------------------------

void setServoAngle(uint8_t channel, int angle)
{
  angle = constrain(angle, 0, 180);

  uint16_t pulse = map(angle, 0, 180, SERVOMIN, SERVOMAX);

  pwm.setPWM(channel, 0, pulse);
}

//--------------------------------------------------

void toggleServo(uint8_t index)
{
  servoState[index] = !servoState[index];

  int angle = servoState[index] ?
              activeAngle[index] :
              homeAngle[index];

  setServoAngle(servoChannel[index], angle);

  Serial.print("Servo CH");
  Serial.print(servoChannel[index]);
  Serial.print(" -> ");
  Serial.print(angle);
  Serial.println(" deg");
}

//--------------------------------------------------

void setup()
{
  Serial.begin(115200);

  Wire.begin(SDA_PIN, SCL_PIN);

  pwm.begin();
  pwm.setPWMFreq(SERVO_FREQ);

  delay(500);

  // Đưa toàn bộ servo về vị trí ban đầu
  for (int i = 0; i < 5; i++)
  {
    setServoAngle(servoChannel[i], homeAngle[i]);
  }

  Serial.println("================================");
  Serial.println("Servo Toggle Control");
  Serial.println("================================");
  Serial.println("Nhan:");
  Serial.println("1 -> Servo CH0");
  Serial.println("2 -> Servo CH2");
  Serial.println("3 -> Servo CH4");
  Serial.println("4 -> Servo CH6");
  Serial.println("5 -> Servo CH8");
  Serial.println();
}

//--------------------------------------------------

void loop()
{
  while (Serial.available())
  {
    char cmd = Serial.read();

    switch (cmd)
    {
      case '1':
        toggleServo(0);
        break;

      case '2':
        toggleServo(1);
        break;

      case '3':
        toggleServo(2);
        break;

      case '4':
        toggleServo(3);
        break;

      case '5':
        toggleServo(4);
        break;
    }
  }
}