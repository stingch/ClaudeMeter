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

#define COLOR_READY_GREEN   0x07E0       // 就緒狀態亮綠色 (TFT_GREEN)
#define COLOR_BAR_FILL      0x9E2B       // 進度條填充綠色
#define COLOR_CLAUDE_ORANGE 0xE4CF       // 吉祥物/Usage字體/星芒主題粉橘色 (#E4CF)

// ================= UI 佈局常數 =================
#define SCREEN_W  320
#define SCREEN_H  240

// -------------------------------------------------------------
// Claude 像素小人點陣圖 (32x32 像素, 單色 128 Bytes)
// -------------------------------------------------------------
const uint8_t PROGMEM claude_mascot_32x32[128] = {
  0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
  0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
  0x0F, 0xFF, 0xFF, 0xF0, 0x0F, 0xFF, 0xFF, 0xF0, 0x0F, 0xFF, 0xFF, 0xF0,
  0x0F, 0x3F, 0xFC, 0xF0, 0x0F, 0x3F, 0xFC, 0xF0, 0x0F, 0x3F, 0xFC, 0xF0,
  0x0F, 0xFF, 0xFF, 0xF0, 0x0F, 0xFF, 0xFF, 0xF0, 0x0F, 0xFF, 0xFF, 0xF0,
  0x0F, 0xFF, 0xFF, 0xF0, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
  0x0F, 0xFF, 0xFF, 0xF0, 0x0F, 0xFF, 0xFF, 0xF0, 0x0F, 0xFF, 0xFF, 0xF0,
  0x0D, 0x80, 0x01, 0xB0, 0x0D, 0x80, 0x01, 0xB0, 0x0D, 0x80, 0x01, 0xB0,
  0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
  0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
  0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00
};

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

// 繪製吉祥物小人 (帶 Y 軸動態懸浮)
void drawClaudeMascot(int x, int y, float offsetY) {
  int renderY = y + (int)(offsetY);
  spr.drawBitmap(x, renderY, claude_mascot_32x32, 32, 32, COLOR_CLAUDE_ORANGE);
}

// 繪製單一數據卡片
void drawClawdmeterCard(int x, int y, int w, int h, const char* tagText, float percent, String resetTime) {
  percent = constrain(percent, 0.0, 100.0);

  // 1. 卡片背景與邊框
  spr.fillRoundRect(x, y, w, h, 8, COLOR_CARD);
  spr.drawRoundRect(x, y, w, h, 8, COLOR_CARD_BORDER);

  // 2. 左上角：百分比數值
  spr.setTextColor(COLOR_TEXT_MAIN, COLOR_CARD);
  spr.setTextDatum(TL_DATUM);
  String pctStr = String((int)round(percent)) + "%";
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

  // 4. 中間：進度條
  int barX = x + 14;
  int barY = y + 44;
  int barW = w - 28;
  int barH = 10;

  spr.fillRoundRect(barX, barY, barW, barH, 5, COLOR_BAR_BG);
  int fillW = (int)((percent / 100.0) * barW);
  if (fillW > 0) {
    if (fillW < 10) {
      spr.fillRect(barX, barY, fillW, barH, COLOR_BAR_FILL);
    } else {
      spr.fillRoundRect(barX, barY, fillW, barH, 5, COLOR_BAR_FILL);
    }
  }

  // 5. 底部：重置時間提示
  String resetLabel = "";
  uint16_t labelColor = COLOR_TEXT_MUTED;

  if (resetTime == "Ready" || percent == 0.0) {
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

  // --- 1. Header: 動態吉祥物小人 + 內建字型 "Usage" 置中排版 ---
  spr.setTextFont(4); // 使用 4 號字型，高度與比例與原本 32px 的小人相稱
  const char* usageText = "Usage";
  int usageTextW = spr.textWidth(usageText);
  int mascotW = 32;
  int padding = 12; 

  int headerTotalW = mascotW + padding + usageTextW;
  int headerStartX = (SCREEN_W - headerTotalW) / 2;

  // 繪製跳動的小人
  float mascotOffsetY = sin(animAngle) * 5.0f;
  drawClaudeMascot(headerStartX, 8, mascotOffsetY);

  // 繪製內建字型標題 "Usage"
  spr.setTextColor(TFT_WHITE, COLOR_BG);
  spr.setTextDatum(TL_DATUM);
  spr.drawString(usageText, headerStartX + mascotW + padding, 12); // Y 軸稍微下移至 12 讓文字與小人垂直居中

  // --- 2. 數據卡片 ---
  drawClawdmeterCard(14, 46, 292, 82, "Current", currentSessionPct, sessionResetStr);
  drawClawdmeterCard(14, 134, 292, 82, "Weekly", currentWeeklyPct, weeklyResetStr);

  // --- 3. 底部: 旋轉星芒 + "Baking" 動態文字區塊 ---
  spr.setTextFont(2);
  
  const char* bakingBaseText = "Baking";
  int bakingBaseW = spr.textWidth(bakingBaseText);
  int starW = 32; 
  int starBakingPadding = 8; 

  int bottomFixedTotalW = starW + starBakingPadding + bakingBaseW;
  int bottomStartX = (SCREEN_W - bottomFixedTotalW) / 2; 

  // 繪製旋轉星芒
  int starCx = bottomStartX + (starW / 2);
  drawRotatedStar(starCx, 226, starAngle, COLOR_CLAUDE_ORANGE);

  // 繪製固定文字 "Baking"
  int bakingTextStartX = bottomStartX + starW + starBakingPadding;
  spr.setTextColor(COLOR_CLAUDE_ORANGE, COLOR_BG);
  spr.setTextDatum(TL_DATUM);
  spr.drawString(bakingBaseText, bakingTextStartX, 218);

  // 繪製動態省略號
  String dots = "";
  if (dotFrame == 1) dots = "."; 
  else if (dotFrame == 2) dots = ".."; 
  else if (dotFrame == 3) dots = "...";

  spr.drawString(dots, bakingTextStartX + bakingBaseW, 218);

  // 推送雙緩衝畫布至屏幕
  spr.pushSprite(0, 0);
}

// BLE 寫入與連線狀態回調
class MyCallbacks: public BLECharacteristicCallbacks {
    void onWrite(BLECharacteristic *pCharacteristic) {
      uint8_t* pData = pCharacteristic->getData();
      size_t length = pCharacteristic->getLength();

      if (length >= 8) {
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

        // 解析亮度控制
        if (length >= 9) setBacklight(pData[8]);

        Serial.printf("BLE Data Updated! Session: %.0f%%, Weekly: %.0f%%\n", targetSessionPct, targetWeeklyPct);
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

  // 1. 小人懸浮動畫角度遞增
  animAngle += 0.15f; 
  if (animAngle > 6.283185f) animAngle = 0.0f;

  // 2. 星芒旋轉角度遞增
  starAngle += 6.0f; 
  if (starAngle >= 360.0f) starAngle -= 360.0f;

  // 3. Baking 省略號動畫幀控制
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