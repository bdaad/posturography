#include <SPI.h>

// ==== ピンアサイン ====
// ESP32 側
const int PIN_MISO = 19;   // DOUT/RDY 共通
const int PIN_MOSI = 23;   // DIN
const int PIN_SCLK = 18;   // SCLK
const int PIN_SYNC = 21;   // SYNC（今回は常時 HIGH）

// AD7193 ごとの CS
const int CS1 = 5;
const int CS2 = 17;
const int CS3 = 27;
const int CS4 = 25;

// ==== SPI 設定 ====
// SCLK 1 MHz, MODE3（AD7193 データシート推奨）
SPISettings ad7193SPISettings(1000000, MSBFIRST, SPI_MODE3);

// ==== AD7193 のレジスタアドレス（RS2..0） ====
const uint8_t REG_STATUS = 0b000;
const uint8_t REG_MODE   = 0b001;
const uint8_t REG_CONFIG = 0b010;
const uint8_t REG_DATA   = 0b011;
const uint8_t REG_ID     = 0b100;
const uint8_t REG_GPOCON = 0b101;
// 110,111 は OFFSET/FULL-SCALE で今回は未使用

// ==== 共通: COMM レジスタバイト生成 ====
// isRead=true で読み出し（R/!W = 1）
// regAddr は上の REG_xxx（0〜7）
uint8_t buildCommByte(bool isRead, uint8_t regAddr) {
  uint8_t comm = 0;
  // ビット7: WEN=0（書き込み許可）
  // ビット6: R/!W
  if (isRead) {
    comm |= 0x40;  // 0b0100_0000
  }
  // ビット5..3: RS2..RS0 = レジスタアドレス
  comm |= (regAddr & 0x07) << 3;
  // ビット2: CREAD=0（連続リード無効）
  // ビット1..0: Don't care
  return comm;
}

// ==== リセット: 40 個以上の 1 を送る ====
// データシート推奨：0xFF を 6〜8 回送信
void ad7193Reset(int csPin) {
  digitalWrite(csPin, LOW);
  SPI.beginTransaction(ad7193SPISettings);
  for (int i = 0; i < 8; i++) {
    SPI.transfer(0xFF);
  }
  SPI.endTransaction();
  digitalWrite(csPin, HIGH);

  delay(5);  // 内部リセット完了待ち
}

// ==== 24bit レジスタ書き込み（MODE/CONFIG 用） ====
void ad7193WriteReg24(int csPin, uint8_t regAddr, uint32_t value) {
  uint8_t b2 = (value >> 16) & 0xFF;  // MSB
  uint8_t b1 = (value >> 8) & 0xFF;
  uint8_t b0 = value & 0xFF;          // LSB

  digitalWrite(csPin, LOW);
  SPI.beginTransaction(ad7193SPISettings);

  uint8_t comm = buildCommByte(false, regAddr); // write
  SPI.transfer(comm);

  SPI.transfer(b2);
  SPI.transfer(b1);
  SPI.transfer(b0);

  SPI.endTransaction();
  digitalWrite(csPin, HIGH);
}

// ==== 24bit レジスタ読み出し ====
// MODE/CONFIG/DATA の確認用
uint32_t ad7193ReadReg24(int csPin, uint8_t regAddr) {
  uint32_t value = 0;

  digitalWrite(csPin, LOW);
  SPI.beginTransaction(ad7193SPISettings);

  uint8_t comm = buildCommByte(true, regAddr); // read
  SPI.transfer(comm);

  uint8_t b2 = SPI.transfer(0x00);
  uint8_t b1 = SPI.transfer(0x00);
  uint8_t b0 = SPI.transfer(0x00);

  SPI.endTransaction();
  digitalWrite(csPin, HIGH);

  value = ((uint32_t)b2 << 16) | ((uint32_t)b1 << 8) | b0;
  return value;
}

// ==== ステータスレジスタ（8bit）読み出し ====
// RDY ビット（bit7）を使って変換完了判定
uint8_t ad7193ReadStatus(int csPin) {
  uint8_t status = 0;

  digitalWrite(csPin, LOW);
  SPI.beginTransaction(ad7193SPISettings);

  uint8_t comm = buildCommByte(true, REG_STATUS);
  SPI.transfer(comm);

  status = SPI.transfer(0x00);

  SPI.endTransaction();
  digitalWrite(csPin, HIGH);

  return status;
}

// ==== RDY=0 になるまで STATUS をポーリング ====
// RDY ビット (bit7) = 0 → 新しいデータが DATA レジスタにある
void ad7193WaitForReadyStatus(int csPin) {
  while (true) {
    uint8_t status = ad7193ReadStatus(csPin);
    if ((status & 0x80) == 0) {  // RDY==0
      break;
    }
    // あまり SPI を連打しすぎないように 50us 程度の待ち
    delayMicroseconds(50);
  }
}

// ==== DATA レジスタから 24bit 生コード取得 ====
// 24bit オフセットバイナリを 32bit に格納
uint32_t ad7193ReadData(int csPin) {
  uint32_t code = 0;

  digitalWrite(csPin, LOW);
  SPI.beginTransaction(ad7193SPISettings);

  uint8_t comm = buildCommByte(true, REG_DATA);
  SPI.transfer(comm);

  uint8_t b2 = SPI.transfer(0x00);
  uint8_t b1 = SPI.transfer(0x00);
  uint8_t b0 = SPI.transfer(0x00);

  SPI.endTransaction();
  digitalWrite(csPin, HIGH);

  code = ((uint32_t)b2 << 16) | ((uint32_t)b1 << 8) | b0;
  return code;
}

// ==== MODE レジスタを「約 100.1 Hz」に設定 ====
// ・内部クロック (CLK1:CLK0 = 10)
// ・sinc4, chop 無効, fast settling/zero latency 無効
// ・FS[9:0] = 48 → fADC ≒ (4.92MHz / 1024) / 48 ≒ 100.1 Hz
void ad7193SetMode_100Hz(int csPin) {
  // まず、現在の MODE を読み出し（デバッグ用）
  uint32_t mode_before = ad7193ReadReg24(csPin, REG_MODE);

  // MODE レジスタ新値を組み立て
  uint32_t mode = 0;
  const uint16_t FS = 48;  // ここを変えると出力データレートが変わる

  // MD2..0 = 000: continuous conversion
  mode |= (0b000UL << 21);
  // DAT_STA = 0（ステータス付加しない）
  // CLK1:CLK0 = 10: 内部 4.92MHz クロック
  mode |= (0b10UL << 18);
  // AVG1:AVG0 = 00: fast settling 無効
  // SINC3 = 0: sinc4 フィルタ
  // ENPAR = 0: parity 無効
  // CLK_DIV = 0
  // Single = 0: zero latency 無効
  // REJ60 = 0: 60Hz notch 追加なし
  // FS9..0 = 48
  mode |= (uint32_t)(FS & 0x03FF);

  // 書き込み
  ad7193WriteReg24(csPin, REG_MODE, mode);

  // 書き込み後の MODE 読み出し
  uint32_t mode_after = ad7193ReadReg24(csPin, REG_MODE);

  // デバッグ出力
  Serial.print("AD7193 MODE (before) @CS");
  Serial.print(csPin);
  Serial.print(" = 0x");
  Serial.println(mode_before, HEX);

  Serial.print("AD7193 MODE (after ) @CS");
  Serial.print(csPin);
  Serial.print(" = 0x");
  Serial.println(mode_after, HEX);
}

// ==== CONFIG レジスタ初期化 ====
// ここでは：
// ・差動入力 AIN1-AIN2（CH0）
// ・バッファ ON (BUF=1)
// ・バイポーラ (U/B=0)
// ・ゲインはデフォルトの 128 (G2..0=111) のままでもよいが、
//   必要に応じて 1 倍 (000) に変更しても良い。
void ad7193SetConfig(int csPin) {
  // デフォルト値 0x000117 をベースに、
  // 必要があればゲイン等を変更する。
  uint32_t config = 0;

  // Chop=0, REFSEL=0, pseudo=0, short=0, temp=0
  // CH0=1, その他の CHx=0 → AIN1-AIN2
  config |= (1UL << 8);    // CH0=1

  // Burn=0, REFDET=0
  // BUF=1（入力バッファ ON）
  config |= (1UL << 4);

  // U/B=0（バイポーラ）
  // G2..0=111 → ゲイン 128
  config |= 0x07;  // G2..0

  ad7193WriteReg24(csPin, REG_CONFIG, config);

  uint32_t config_after = ad7193ReadReg24(csPin, REG_CONFIG);
  Serial.print("AD7193 CONFIG @CS");
  Serial.print(csPin);
  Serial.print(" = 0x");
  Serial.println(config_after, HEX);
}

// ==== 1 チップ分の初期化 ====
// 1) ソフトリセット
// 2) MODE レジスタ (FS=48) 設定
// 3) CONFIG レジスタ設定
// 4) 1 回 RDY=0 まで待って古い変換結果を捨てる
void ad7193InitOne(int csPin) {
  ad7193Reset(csPin);

  // ID レジスタ確認（オプション）
  digitalWrite(csPin, LOW);
  SPI.beginTransaction(ad7193SPISettings);
  uint8_t comm = buildCommByte(true, REG_ID);
  SPI.transfer(comm);
  uint8_t id = SPI.transfer(0x00);
  SPI.endTransaction();
  digitalWrite(csPin, HIGH);

  Serial.print("AD7193 ID @CS");
  Serial.print(csPin);
  Serial.print(" = 0x");
  Serial.println(id, HEX);

  // MODE/CONFIG 設定
  ad7193SetMode_100Hz(csPin);
  ad7193SetConfig(csPin);

  // 一度 RDY=0 になるのを待って、古いデータを捨てる
  ad7193WaitForReadyStatus(csPin);
  (void)ad7193ReadData(csPin);
}

void setup() {
  Serial.begin(115200);
  delay(2000);  // シリアルモニタ接続待ち（任意）

  // ピン初期化
  pinMode(PIN_MISO, INPUT);
  pinMode(PIN_MOSI, OUTPUT);
  pinMode(PIN_SCLK, OUTPUT);
  pinMode(PIN_SYNC, OUTPUT);
  digitalWrite(PIN_SYNC, HIGH);  // SYNC は常時 HIGH（今回は未使用）

  pinMode(CS1, OUTPUT);
  pinMode(CS2, OUTPUT);
  pinMode(CS3, OUTPUT);
  pinMode(CS4, OUTPUT);
  digitalWrite(CS1, HIGH);
  digitalWrite(CS2, HIGH);
  digitalWrite(CS3, HIGH);
  digitalWrite(CS4, HIGH);

  SPI.begin(PIN_SCLK, PIN_MISO, PIN_MOSI);

  Serial.println("AD7193 x4 initialized (100 Hz target).");
  Serial.println("Output format: ch1,ch2,ch3,ch4 (raw code)");

  // 各 AD7193 を初期化
  ad7193InitOne(CS1);
  ad7193InitOne(CS2);
  ad7193InitOne(CS3);
  ad7193InitOne(CS4);
}

void loop() {
  // CS1 の STATUS レジスタの RDY ビットを監視し、
  // 「新しい変換結果」が出たタイミングで 4 チップ分を読み出す。
  ad7193WaitForReadyStatus(CS1);

  uint32_t ch1 = ad7193ReadData(CS1);
  uint32_t ch2 = ad7193ReadData(CS2);
  uint32_t ch3 = ad7193ReadData(CS3);
  uint32_t ch4 = ad7193ReadData(CS4);

  // 1 サンプル 1 行（4 チャンネル分）
  Serial.print(ch1);
  Serial.print(",");
  Serial.print(ch2);
  Serial.print(",");
  Serial.print(ch3);
  Serial.print(",");
  Serial.println(ch4);
}
