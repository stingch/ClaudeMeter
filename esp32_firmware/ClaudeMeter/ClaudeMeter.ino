// =============================================================
// ClaudeMeter 韌體 - Arduino IDE 版
//
// 這份檔案的內容和 esp32_firmware/src/main.cpp（PlatformIO 版）相同，
// 改其中一份時記得把另一份也同步更新。
//
// Arduino IDE 不會讀取 platformio.ini 的 build_flags，螢幕驅動與腳位
// 要設定在 TFT_eSPI 函式庫資料夾裡的 User_Setup.h：
//   ILI9341_DRIVER, TFT_WIDTH 240, TFT_HEIGHT 320,
//   TFT_MOSI 23, TFT_SCLK 18, TFT_CS 5, TFT_DC 16, TFT_RST 17,
//   TFT_BL 21, LOAD_FONT2, LOAD_FONT4, SPI_FREQUENCY 40000000
// =============================================================

#include <Arduino.h>

#include <BLEDevice.h>
#include <BLEUtils.h>
#include <BLEServer.h>
#include <TFT_eSPI.h>
#include <esp_arduino_version.h>

TFT_eSPI tft = TFT_eSPI();
TFT_eSprite spr = TFT_eSprite(&tft);

// ================= BLE UUID 設定 =================
#define SERVICE_UUID        "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
#define CHARACTERISTIC_UUID "beb5483e-36e1-4688-b7f5-ea07361b26a8"

// ================= 背光 PWM 控制設定 =================
#define BLK_PIN          21
#define PWM_CHANNEL      0
#define PWM_FREQ         20000
#define PWM_RESOLUTION   8

uint8_t currentBrightness = 200;

// ================= UI 色彩定義 (RGB565) =================
#define COLOR_BG            TFT_BLACK    // 全局背景黑 (0x0000)
#define COLOR_CARD          0x0841       // 深灰卡片背景 (~#0A0A0A)
#define COLOR_CARD_BORDER   0x1082       // 卡片邊框灰
#define COLOR_BAR_BG        0x1082       // 進度條未填充底色
#define COLOR_TAG_BG        0x1082       // Pills 標籤背景深灰

#define COLOR_TEXT_MAIN     TFT_WHITE    // 主文字純白
#define COLOR_TAG_TEXT      TFT_WHITE    // Pills 標籤文字白
#define COLOR_TEXT_MUTED    0x9CD3       // 次要/重置時間文字灰
#define COLOR_TEXT_DIM      0x52AA       // 無數據時的佔位文字（更暗的灰）

#define COLOR_READY_GREEN   0x07E0       // 就緒狀態亮綠色 (TFT_GREEN)
#define COLOR_BAR_FILL      0x9E2B       // 進度條填充綠色（低用量）
#define COLOR_BAR_MID       0xFD20       // 進度條中段琥珀色
#define COLOR_BAR_HIGH      0xF9A6       // 進度條高用量紅色

// 進度條漸變門檻 (%)：低於 MID_START 純綠；MID_START→MID_FULL 綠漸變到琥珀；
// HIGH_START→HIGH_FULL 琥珀漸變到紅；超過 HIGH_FULL 純紅
#define BAR_MID_START   50.0f
#define BAR_MID_FULL    70.0f
#define BAR_HIGH_START  80.0f
#define BAR_HIGH_FULL   95.0f
#define COLOR_CLAUDE_ORANGE 0xE4CF       // 吉祥物/Usage字體/星芒主題粉橘色 (#E4CF)
#define COLOR_WARN_AMBER    0xFD20       // 異常狀態警示琥珀色

// ================= UI 佈局常數 =================
#define SCREEN_W  320
#define SCREEN_H  240

// ---- 標題區吉祥物 ----
// 點陣圖只有第 6~25 列有圖案，上下都是空白，放大時只畫這段，標題區才放得下
#define MASCOT_SCALE            1.7f  // 放大倍率，可用小數；1 = 原尺寸。上限約 2.2，再大會碰到下面的卡片
                                       // 非整數時每格會交錯畫成不同寬度（例如 1px / 2px），細節會稍微不均勻
#define MASCOT_ROW_TOP          6      // 點陣圖有圖案的第一列
#define MASCOT_ROW_BOTTOM       25     // 點陣圖有圖案的最後一列
#define MASCOT_HOVER_PX         3.0f   // 上下彈跳幅度 (px)
#define MASCOT_WAVES_PER_BOUNCE 4      // 每彈跳一個來回擺幾次手（整數，動畫循環才接得上）
#define HEADER_CY          23     // 標題列（小人 + "Usage"）的垂直中心

// ================= 資料狀態機 =================
// 舊版韌體只要收到 BLE 寫入就更新數字，從來不判斷「這份數據是不是有效的」，
// 也不判斷「上次收到是多久以前」。結果是 PC 端掛掉或抓不到額度時，螢幕會
// 停在最後一次的數字、動畫照跑，看起來完全正常 —— 你根本不會發現它壞了。
// 這裡補上狀態機，讓螢幕永遠誠實地說出自己知道什麼。
enum DataState {
  STATE_WAITING,   // 開機後還沒收到過任何資料
  STATE_OK,        // 有正常的額度數據
  STATE_NO_DATA,   // PC 端連上了，但 API 沒有額度可回報（例如免費方案）
  STATE_STALE      // 曾經有資料，但太久沒更新了（PC 端或 BLE 斷了）
};

DataState dataState = STATE_WAITING;
unsigned long lastUpdateMs = 0;

// 超過這個時間沒收到 BLE 寫入就視為離線。
// PC 端預設每 60 秒送一次，所以 5 分鐘等於連續漏掉 5 次。
#define STALE_TIMEOUT_MS 300000UL

// PC 端封包第 10 個 byte 的狀態旗標（對應 common.py 的 STATUS_* 常數）
#define STATUS_OK       0
#define STATUS_NO_DATA  1

// -------------------------------------------------------------
// Claude 像素小人點陣圖 (32x32 像素, 單色 128 Bytes)
// -------------------------------------------------------------
const uint8_t PROGMEM claude_mascot_32x32[128] = {
  // 第 0 - 5 行：上方空白 (6 行)
  0x00, 0x00, 0x00, 0x00,
  0x00, 0x00, 0x00, 0x00,
  0x00, 0x00, 0x00, 0x00,
  0x00, 0x00, 0x00, 0x00,
  0x00, 0x00, 0x00, 0x00,
  0x00, 0x00, 0x00, 0x00,

  // 第 6 - 9 行：頭部頂端 (4 行)
  0x0F, 0xFF, 0xFF, 0xF0,
  0x0F, 0xFF, 0xFF, 0xF0,
  0x0F, 0xFF, 0xFF, 0xF0,
  0x0F, 0xFF, 0xFF, 0xF0,

  // 第 10 - 13 行：眼睛 (4 行)
  0x0F, 0x3F, 0xFC, 0xF0,
  0x0F, 0x3F, 0xFC, 0xF0,
  0x0F, 0x3F, 0xFC, 0xF0,
  0x0F, 0x3F, 0xFC, 0xF0,

  // 第 14 - 15 行：眼睛下方身體 (2 行)
  0x0F, 0xFF, 0xFF, 0xF0,
  0x0F, 0xFF, 0xFF, 0xF0,

  // 第 16 - 19 行：手臂與身體 (4 行)
  0xFF, 0xFF, 0xFF, 0xFF,
  0xFF, 0xFF, 0xFF, 0xFF,
  0xFF, 0xFF, 0xFF, 0xFF,
  0xFF, 0xFF, 0xFF, 0xFF,

  // 第 20 - 21 行：手臂下方身體 (2 行)
  0x0F, 0xFF, 0xFF, 0xF0,
  0x0F, 0xFF, 0xFF, 0xF0,

  // 第 22 - 25 行：四條腿 (4 行，左右對稱：第 4-6、9-11、20-22、25-27 欄)
  0x0E, 0x70, 0x0E, 0x70,
  0x0E, 0x70, 0x0E, 0x70,
  0x0E, 0x70, 0x0E, 0x70,
  0x0E, 0x70, 0x0E, 0x70,

  // 第 26 - 31 行：下方空白 (6 行)
  0x00, 0x00, 0x00, 0x00,
  0x00, 0x00, 0x00, 0x00,
  0x00, 0x00, 0x00, 0x00,
  0x00, 0x00, 0x00, 0x00,
  0x00, 0x00, 0x00, 0x00,
  0x00, 0x00, 0x00, 0x00
};

// ---- 擺手動畫 ----
// 點陣圖第 16~19 列、身體兩側各凸出 4 格的部分就是手臂。
// 畫的時候身體照原圖畫，手臂另外以「手肘」（手臂和身體的接點）為支點旋轉，
// 手肘固定不動，只有手臂上下擺。座標單位是點陣圖的格子，這裡設定右手，左手自動鏡像。
#define ARM_ROW_TOP     16      // 手臂所在的第一列
#define ARM_ROWS        4       // 點陣圖上手臂佔幾列（畫身體時要跳過這幾列的手臂格子）
#define ARM_THICK       2.8f    // 實際畫出來的手臂粗細（格，可用小數）；比 ARM_ROWS 小就會比原圖細
#define ARM_LENGTH      6       // 從支點量起的手臂長度（格）；支點在身體內 1 格，露出來的是 4 格
#define ARM_ELBOW_X     27.0f   // 右手手肘（轉動支點）X：身體右邊緣往內 1 格，轉動時接縫才不會缺角
#define ARM_ELBOW_Y     18.0f   // 右手手肘 Y（手臂粗細的中線）
#define ARM_SWING_DEG   30.0f   // 往上、往下各擺幾度

// -------------------------------------------------------------
// Claude 多角星芒點陣圖 (32x32 像素, 單色 128 Bytes)
// -------------------------------------------------------------
const uint8_t PROGMEM claude_star_32x32[128] = {
  0x00, 0xE0, 0x00, 0x00, 0x01, 0xE0, 0x70, 0x00,
  0x01, 0xF0, 0x70, 0x00, 0x00, 0xF0, 0x70, 0x20,
  0x00, 0xF8, 0x70, 0xF0, 0x00, 0x78, 0x71, 0xF0,
  0x18, 0x3C, 0x63, 0xE0, 0x3E, 0x3C, 0x63, 0xE0,
  0x1F, 0x1E, 0x67, 0xC0, 0x0F, 0x9E, 0xEF, 0x80,
  0x07, 0xEF, 0xFF, 0x00, 0x01, 0xFF, 0xFF, 0x00,
  0x00, 0xFF, 0xFE, 0x00, 0x00, 0x3F, 0xFC, 0x7F,
  0x00, 0x1F, 0xFF, 0xFF, 0xFF, 0xEF, 0xFF, 0xF8,
  0x7F, 0xFF, 0xFF, 0x00, 0x00, 0x0F, 0xFF, 0xF8,
  0x00, 0x1F, 0xFF, 0xFF, 0x00, 0x7F, 0xFC, 0x7F,
  0x01, 0xF7, 0xFE, 0x06, 0x03, 0xEF, 0xFF, 0x00,
  0x0F, 0x9D, 0xFF, 0x80, 0x1F, 0x1D, 0x9D, 0xC0,
  0x1C, 0x39, 0x9E, 0xE0, 0x00, 0x71, 0x8F, 0x30,
  0x00, 0xE1, 0x87, 0x18, 0x00, 0xE3, 0x87, 0x80,
  0x01, 0xC3, 0x83, 0x80, 0x00, 0x83, 0x81, 0x00,
  0x00, 0x03, 0x80, 0x00, 0x00, 0x03, 0x00, 0x00
};

// ================= 全局 UI 與動畫變數 =================
float currentSessionPct = 0.0;
float targetSessionPct  = 0.0;

float currentWeeklyPct  = 0.0;
float targetWeeklyPct   = 0.0;

String sessionResetStr = "Ready";
String weeklyResetStr  = "Ready";

bool bleConnected      = false;
bool shouldRestartAdv  = false;

float animAngle = 0.0f;  // 小人懸浮正弦波角度
float starAngle = 0.0f;  // 星芒順時針旋轉角度
int dotFrame    = 0;     // Baking 省略號動態幀 (0~3)
int frameCount  = 0;     // 畫格計數器

// 設置背光亮度 (相容 ESP32 Arduino SDK v2 與 v3)
void setBacklight(uint8_t brightness) {
  currentBrightness = brightness;
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcWrite(BLK_PIN, brightness);
#else
  ledcWrite(PWM_CHANNEL, brightness);
#endif
}

// 目前狀態下，卡片上的數字是否可信
bool hasUsableData() {
  return dataState == STATE_OK || dataState == STATE_STALE;
}

// 旋轉星芒渲染 (反向採樣)
void drawRotatedStar(int cx, int cy, float angleDegree, uint16_t color) {
  float rad = angleDegree * 0.0174532925f;
  float cosA = cos(rad);
  float sinA = sin(rad);

  for (int dy = -16; dy < 16; dy++) {
    for (int dx = -16; dx < 16; dx++) {
      float srcX = dx * cosA + dy * sinA + 16.0f;
      float srcY = -dx * sinA + dy * cosA + 16.0f;

      int sx = (int)floor(srcX);
      int sy = (int)floor(srcY);

      if (sx >= 0 && sx < 32 && sy >= 0 && sy < 32) {
        int byteIdx = sy * 4 + (sx / 8);
        int bitIdx  = 7 - (sx % 8);

        if (pgm_read_byte(&claude_star_32x32[byteIdx]) & (1 << bitIdx)) {
          spr.drawPixel(cx + dx, cy + dy, color);
        }
      }
    }
  }
}

// 這一格是不是手臂（身體兩側凸出的部分）
bool isMascotArmCell(int row, int col) {
  bool inRows = row >= ARM_ROW_TOP && row < ARM_ROW_TOP + ARM_ROWS;
  bool inCols = col < 4 || col >= 28;   // 原圖身體是第 4~27 欄，兩側凸出的就是手
  return inRows && inCols;
}

// 點陣圖格子 (gx, gy) 畫到螢幕上；top 是 MASCOT_ROW_TOP 那一列的螢幕 Y
// 每格的左上、右下邊界都由「格子座標 × 倍率」取整得到，倍率是小數時相鄰格子也能無縫接上
void fillMascotCell(int x, int top, int gx, int gy) {
  int ry = gy - MASCOT_ROW_TOP;
  int x0 = x   + (int)floorf(gx * MASCOT_SCALE);
  int x1 = x   + (int)floorf((gx + 1) * MASCOT_SCALE);
  int y0 = top + (int)floorf(ry * MASCOT_SCALE);
  int y1 = top + (int)floorf((ry + 1) * MASCOT_SCALE);
  spr.fillRect(x0, y0, x1 - x0, y1 - y0, COLOR_CLAUDE_ORANGE);
}

// 畫一隻手臂：以手肘為支點旋轉 angleDeg 度（負數 = 往上）；mirror = true 時畫左手
// 以螢幕像素為單位判斷每個點是否落在旋轉後的手臂長方形內，斜的時候也是一條直的手臂，
// 不會像以格子為單位那樣變成階梯狀
void drawMascotArm(int x, int top, float angleDeg, bool mirror) {
  float rad = angleDeg * 0.0174532925f;
  float cosA = cos(rad);
  float sinA = sin(rad);
  float dir = mirror ? -1.0f : 1.0f;

  // 手肘在點陣圖上的座標（格），左手是右手的鏡像
  float ex = mirror ? (32.0f - ARM_ELBOW_X) : ARM_ELBOW_X;
  float ey = ARM_ELBOW_Y - MASCOT_ROW_TOP;

  int reach = (int)ceilf((ARM_LENGTH + ARM_THICK) * MASCOT_SCALE);
  int cx = x + (int)lroundf(ex * MASCOT_SCALE);
  int cy = top + (int)lroundf(ey * MASCOT_SCALE);

  for (int py = cy - reach; py <= cy + reach; py++) {
    for (int px = cx - reach; px <= cx + reach; px++) {
      // 像素中心換算回點陣圖座標，再轉成沿著手臂的座標 (u = 長度方向, v = 粗細方向)
      float dx = ((px + 0.5f - x) / MASCOT_SCALE - ex) * dir;
      float dy = (py + 0.5f - top) / MASCOT_SCALE - ey;
      float u =  dx * cosA + dy * sinA;
      float v = -dx * sinA + dy * cosA;
      // u 從 -1 開始，讓手臂根部稍微埋進身體，轉動時接縫不會出現缺口
      if (u >= -1.0f && u < ARM_LENGTH && fabs(v) <= ARM_THICK / 2.0f) {
        spr.drawPixel(px, py, COLOR_CLAUDE_ORANGE);
      }
    }
  }
}

// 繪製吉祥物小人 (以 cy 為垂直中心，依 MASCOT_SCALE 放大)
//   offsetY  : 整體上下彈跳位移 (px)
//   armPhase : 擺手相位 (弧度)，兩手以手肘為支點同步上下擺
void drawClaudeMascot(int x, int cy, float offsetY, float armPhase) {
  const int rows = MASCOT_ROW_BOTTOM - MASCOT_ROW_TOP + 1;
  int top = cy - (int)round(rows * MASCOT_SCALE) / 2 + (int)round(offsetY);

  // 1. 先畫手臂，身體再蓋上去，手臂根部就會藏在身體後面
  float armDeg = -sin(armPhase) * ARM_SWING_DEG;
  drawMascotArm(x, top, armDeg, false);
  drawMascotArm(x, top, armDeg, true);

  // 2. 身體（原圖扣掉手臂的部分）
  for (int gy = MASCOT_ROW_TOP; gy <= MASCOT_ROW_BOTTOM; gy++) {
    for (int gx = 0; gx < 32; gx++) {
      uint8_t bits = pgm_read_byte(&claude_mascot_32x32[gy * 4 + (gx / 8)]);
      if ((bits & (0x80 >> (gx % 8))) && !isMascotArmCell(gy, gx)) {
        fillMascotCell(x, top, gx, gy);
      }
    }
  }
}

// 兩個 RGB565 顏色線性內插，t = 0 → a，t = 1 → b
uint16_t lerpColor565(uint16_t a, uint16_t b, float t) {
  t = constrain(t, 0.0f, 1.0f);
  int ar = (a >> 11) & 0x1F, ag = (a >> 5) & 0x3F, ab = a & 0x1F;
  int br = (b >> 11) & 0x1F, bg = (b >> 5) & 0x3F, bb = b & 0x1F;
  int r  = ar + (int)lroundf((br - ar) * t);
  int g  = ag + (int)lroundf((bg - ag) * t);
  int bl = ab + (int)lroundf((bb - ab) * t);
  return (uint16_t)((r << 11) | (g << 5) | bl);
}

// 依用量決定進度條顏色：綠 → 琥珀 → 紅 的漸變
uint16_t barColorFor(float percent) {
  if (percent < BAR_MID_START) return COLOR_BAR_FILL;
  if (percent < BAR_HIGH_START) {
    float t = (percent - BAR_MID_START) / (BAR_MID_FULL - BAR_MID_START);
    return lerpColor565(COLOR_BAR_FILL, COLOR_BAR_MID, t);
  }
  float t = (percent - BAR_HIGH_START) / (BAR_HIGH_FULL - BAR_HIGH_START);
  return lerpColor565(COLOR_BAR_MID, COLOR_BAR_HIGH, t);
}

// 繪製單一數據卡片
// hasData = false 時顯示 "--" 佔位，而不是假裝用量是 0%
void drawClawdmeterCard(int x, int y, int w, int h, const char* tagText,
                        float percent, String resetTime,
                        bool hasData, const char* placeholder) {
  percent = constrain(percent, 0.0, 100.0);

  // 1. 卡片背景與邊框
  spr.fillRoundRect(x, y, w, h, 8, COLOR_CARD);
  spr.drawRoundRect(x, y, w, h, 8, COLOR_CARD_BORDER);

  // 2. 左上角：百分比數值（無數據時顯示 "--"）
  spr.setTextColor(hasData ? COLOR_TEXT_MAIN : COLOR_TEXT_DIM, COLOR_CARD);
  spr.setTextDatum(TL_DATUM);
  String pctStr = hasData ? (String((int)round(percent)) + "%") : String("--");
  spr.drawString(pctStr, x + 14, y + 10, 4);

  // 3. 右上角：標籤 Pill
  int tagW = 76;
  int tagH = 22;
  int tagX = x + w - tagW - 12;
  int tagY = y + 10;
  spr.fillRoundRect(tagX, tagY, tagW, tagH, 11, COLOR_TAG_BG);
  spr.setTextColor(COLOR_TAG_TEXT, COLOR_TAG_BG);
  spr.setTextDatum(MC_DATUM);
  spr.drawString(tagText, tagX + (tagW / 2), tagY + (tagH / 2), 2);

  // 4. 中間：進度條（無數據時只畫空底槽，不填任何顏色）
  int barX = x + 14;
  int barY = y + 44;
  int barW = w - 28;
  int barH = 10;

  spr.fillRoundRect(barX, barY, barW, barH, 5, COLOR_BAR_BG);
  if (hasData) {
    int fillW = (int)((percent / 100.0) * barW);
    uint16_t fillColor = barColorFor(percent);
    if (fillW > 0) {
      if (fillW < 10) {
        spr.fillRect(barX, barY, fillW, barH, fillColor);
      } else {
        spr.fillRoundRect(barX, barY, fillW, barH, 5, fillColor);
      }
    }
  }

  // 5. 底部：重置時間提示
  String resetLabel = "";
  uint16_t labelColor = COLOR_TEXT_MUTED;

  if (!hasData) {
    resetLabel = placeholder;
    labelColor = COLOR_TEXT_DIM;
  } else if (resetTime == "Ready" || percent == 0.0) {
    resetLabel = "Ready";
    labelColor = COLOR_READY_GREEN;
  } else {
    resetLabel = "Resets at " + resetTime;
    labelColor = COLOR_TEXT_MUTED;
  }

  spr.setTextColor(labelColor, COLOR_CARD);
  spr.setTextDatum(TL_DATUM);
  spr.drawString(resetLabel, x + 14, y + 60, 2);
}

// UI 渲染主邏輯
void renderUI() {
  spr.fillSprite(COLOR_BG);

  bool usable = hasUsableData();

  // --- 1. Header: 動態吉祥物小人 + 內建字型 "Usage" 置中排版 ---
  spr.setTextFont(4); // 使用 4 號字型，高度與比例與原本 32px 的小人相稱
  const char* usageText = "Usage";
  int usageTextW = spr.textWidth(usageText);
  int mascotW = (int)round(32 * MASCOT_SCALE);
  int padding = 12;

  int headerTotalW = mascotW + padding + usageTextW;
  int headerStartX = (SCREEN_W - headerTotalW) / 2;

  // 繪製跳動的小人
  float mascotOffsetY = sin(animAngle) * MASCOT_HOVER_PX;
  float armPhase = animAngle * MASCOT_WAVES_PER_BOUNCE;
  drawClaudeMascot(headerStartX, HEADER_CY, mascotOffsetY, armPhase);

  // 繪製內建字型標題 "Usage"（與小人共用同一條垂直中心線）
  spr.setTextColor(TFT_WHITE, COLOR_BG);
  spr.setTextDatum(ML_DATUM);
  spr.drawString(usageText, headerStartX + mascotW + padding, HEADER_CY);

  // --- 2. 數據卡片 ---
  const char* placeholder =
      (dataState == STATE_NO_DATA) ? "No data from API" : "Waiting for PC";

  drawClawdmeterCard(14, 46, 292, 82, "Current",
                     currentSessionPct, sessionResetStr, usable, placeholder);
  drawClawdmeterCard(14, 134, 292, 82, "Weekly",
                     currentWeeklyPct, weeklyResetStr, usable, placeholder);

  // --- 3. 底部: 旋轉星芒 + 狀態文字區塊 ---
  spr.setTextFont(2);

  String statusText;
  uint16_t statusColor;
  bool showDots = false;

  switch (dataState) {
    case STATE_OK:
      statusText  = "Baking";
      statusColor = COLOR_CLAUDE_ORANGE;
      showDots    = true;
      break;

    case STATE_WAITING:
      statusText  = "Waiting for PC";
      statusColor = COLOR_TEXT_MUTED;
      showDots    = true;
      break;

    case STATE_NO_DATA:
      statusText  = "No usage data";
      statusColor = COLOR_WARN_AMBER;
      break;

    case STATE_STALE:
    default: {
      unsigned long ageMin = (millis() - lastUpdateMs) / 60000UL;
      statusText  = "Offline " + String(ageMin) + "m";
      statusColor = COLOR_WARN_AMBER;
      break;
    }
  }

  int statusW = spr.textWidth(statusText);
  int starW = 32;
  int starStatusPadding = 8;

  int bottomFixedTotalW = starW + starStatusPadding + statusW;
  int bottomStartX = (SCREEN_W - bottomFixedTotalW) / 2;

  // 繪製旋轉星芒（顏色跟著狀態走，異常時整條底部都是琥珀色）
  int starCx = bottomStartX + (starW / 2);
  drawRotatedStar(starCx, 226, starAngle, statusColor);

  // 繪製狀態文字
  int statusTextStartX = bottomStartX + starW + starStatusPadding;
  spr.setTextColor(statusColor, COLOR_BG);
  spr.setTextDatum(TL_DATUM);
  spr.drawString(statusText, statusTextStartX, 218);

  // 繪製動態省略號（只有「進行中」的狀態才需要）
  if (showDots) {
    String dots = "";
    if (dotFrame == 1) dots = ".";
    else if (dotFrame == 2) dots = "..";
    else if (dotFrame == 3) dots = "...";

    spr.drawString(dots, statusTextStartX + statusW, 218);
  }

  // 推送雙緩衝畫布至屏幕
  spr.pushSprite(0, 0);
}

// BLE 寫入與連線狀態回調
class MyCallbacks: public BLECharacteristicCallbacks {
    void onWrite(BLECharacteristic *pCharacteristic) {
      uint8_t* pData = pCharacteristic->getData();
      size_t length = pCharacteristic->getLength();

      if (length >= 8) {
        // 收到任何合法封包就重置離線計時器
        lastUpdateMs = millis();

        // 解析狀態旗標（第 10 個 byte，舊版 PC 端沒有這個欄位就當作正常）
        uint8_t status = (length >= 10) ? pData[9] : STATUS_OK;

        if (status == STATUS_NO_DATA) {
          // PC 端有連上 API，但那邊根本沒有額度數據可回報。
          // 這裡絕對不能顯示 0% —— 那會被誤讀成「額度全滿」。
          dataState = STATE_NO_DATA;
          targetSessionPct = 0;
          targetWeeklyPct  = 0;
          sessionResetStr  = "Ready";
          weeklyResetStr   = "Ready";
          Serial.println("BLE: PC reports NO usage data available");
        } else {
          dataState = STATE_OK;

          // 解析 Session 數據
          targetSessionPct = pData[0];
          if (pData[1] == 0 && pData[2] == 0) {
            sessionResetStr = "Ready";
          } else {
            char sBuf[16];
            snprintf(sBuf, sizeof(sBuf), "%02d:%02d", pData[1], pData[2]);
            sessionResetStr = String(sBuf);
          }

          // 解析 Weekly 數據
          targetWeeklyPct = pData[3];
          if (pData[4] == 0 && pData[5] == 0) {
            weeklyResetStr = "Ready";
          } else {
            char wBuf[24];
            snprintf(wBuf, sizeof(wBuf), "%02d/%02d %02d:%02d", pData[4], pData[5], pData[6], pData[7]);
            weeklyResetStr = String(wBuf);
          }

          Serial.printf("BLE Data Updated! Session: %.0f%%, Weekly: %.0f%%\n",
                        targetSessionPct, targetWeeklyPct);
        }

        // 解析亮度控制
        if (length >= 9) setBacklight(pData[8]);
      }
    }
};

class ServerCallbacks: public BLEServerCallbacks {
    void onConnect(BLEServer* pServer) {
      bleConnected = true;
    }
    void onDisconnect(BLEServer* pServer) {
      bleConnected = false;
      shouldRestartAdv = true;
    }
};

void setup() {
  Serial.begin(115200);

  pinMode(BLK_PIN, OUTPUT);
  digitalWrite(BLK_PIN, LOW);

  tft.init();
  tft.setRotation(1);
  tft.fillScreen(COLOR_BG);

  spr.setColorDepth(8);
  spr.createSprite(320, 240);

  renderUI();

#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcAttachChannel(BLK_PIN, PWM_FREQ, PWM_RESOLUTION, PWM_CHANNEL);
#else
  ledcSetup(PWM_CHANNEL, PWM_FREQ, PWM_RESOLUTION);
  ledcAttachPin(BLK_PIN, PWM_CHANNEL);
#endif

  setBacklight(currentBrightness);

  BLEDevice::init("ClaudeMeter");
  BLEServer *pServer = BLEDevice::createServer();
  pServer->setCallbacks(new ServerCallbacks());

  BLEService *pService = pServer->createService(SERVICE_UUID);
  BLECharacteristic *pCharacteristic = pService->createCharacteristic(
                                         CHARACTERISTIC_UUID,
                                         BLECharacteristic::PROPERTY_READ |
                                         BLECharacteristic::PROPERTY_WRITE
                                       );

  pCharacteristic->setCallbacks(new MyCallbacks());
  pService->start();

  BLEAdvertising *pAdvertising = BLEDevice::getAdvertising();
  pAdvertising->addServiceUUID(SERVICE_UUID);
  pAdvertising->setScanResponse(true);
  BLEDevice::startAdvertising();
}

void loop() {
  if (shouldRestartAdv) {
    shouldRestartAdv = false;
    delay(100);
    BLEDevice::startAdvertising();
  }

  // 0. 離線偵測：太久沒收到 BLE 寫入就切換成 STALE
  //    (millis() 溢位時無號數相減仍然正確，不需要特別處理)
  if (dataState == STATE_OK || dataState == STATE_NO_DATA) {
    if ((millis() - lastUpdateMs) > STALE_TIMEOUT_MS) {
      dataState = STATE_STALE;
      Serial.println("BLE: no update for too long -> STALE");
    }
  }

  // 1. 小人懸浮動畫角度遞增
  animAngle += 0.15f;
  if (animAngle > 6.283185f) animAngle = 0.0f;

  // 2. 星芒旋轉角度遞增
  starAngle += 6.0f;
  if (starAngle >= 360.0f) starAngle -= 360.0f;

  // 3. 狀態文字省略號動畫幀控制
  frameCount++;
  if (frameCount >= 15) {
    frameCount = 0;
    dotFrame = (dotFrame + 1) % 4;
  }

  // 4. 進度條數值平滑漸變 (Lerp)
  currentSessionPct += (targetSessionPct - currentSessionPct) * 0.12f;
  currentWeeklyPct  += (targetWeeklyPct  - currentWeeklyPct)  * 0.12f;

  renderUI();
  delay(16); // 約 60 FPS 的繪製速率
}
