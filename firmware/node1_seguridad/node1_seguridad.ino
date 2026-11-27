// Sentrya - Nodo 1 (Seguridad). ESP32-CAM (AI-Thinker, OV2640) + sensor de sonido SA92
// + buzzer pasivo (sirena) + rele de la cerradura 12 V.
//
// - Camara: stream MJPEG en http://<ip>:81/stream, el que lee el edge con OpenCV.
// - Sonido: cada disparo del SA92 (con debounce) hace POST /api/v1/security/sound.
// - Cada HEARTBEAT_INTERVAL_MS hace POST /api/v1/nodes/node-1/heartbeat con su IP y el estado
//   de la sirena y la cerradura.
// - Atiende POST /command (puerto 80):
//     siren on / off
//     lock  lock / unlock {pulseSeconds}   (unlock = pulso: la cerradura vuelve a bloquearse sola)
//   y responde { "commandId", "status": "APPLIED"|"FAILED", "simulated": bool }.
// - GET / devuelve el estado del nodo y el enlace al stream.
// - Escucha el anuncio UDP del edge (DISCOVERY_PORT) y toma su IP de ahi. EDGE_HOST es el respaldo.
//
// La cerradura nunca se desbloquea sola: solo con la orden unlock, y solo por el pulso.
// Proteccion: sin contacto con el edge, la sirena se apaga.
//
// Bibliotecas: "ArduinoJson" (v7). Placa: "AI Thinker ESP32-CAM".
// Para subir: IO0 a GND, boton RST, Upload; luego quitar IO0 de GND y RST otra vez.

#include <WiFi.h>
#include <WebServer.h>
#include <HTTPClient.h>
#include <WiFiUdp.h>
#include <ArduinoJson.h>
#include "esp_camera.h"
#include "esp_http_server.h"
#include "config.h"

// Pines de la camara AI-Thinker
#define CAM_PWDN 32
#define CAM_RESET -1
#define CAM_XCLK 0
#define CAM_SIOD 26
#define CAM_SIOC 27
#define CAM_Y9 35
#define CAM_Y8 34
#define CAM_Y7 39
#define CAM_Y6 36
#define CAM_Y5 21
#define CAM_Y4 19
#define CAM_Y3 18
#define CAM_Y2 5
#define CAM_VSYNC 25
#define CAM_HREF 23
#define CAM_PCLK 22

WebServer server(80);
WiFiUDP discovery;
httpd_handle_t streamServer = NULL;

String edgeHost = EDGE_HOST;   // se actualiza con el anuncio del edge
int edgePort = EDGE_PORT;

bool cameraOk = false;
bool sirenOn = false;
bool locked = true;
unsigned long sirenSinceMs = 0;
unsigned long unlockUntilMs = 0;

volatile bool soundFlag = false;
unsigned long lastSoundMs = 0;

unsigned long lastHeartbeatMs = 0;
unsigned long lastEdgeOkMs = 0;

// ---------- Camara y stream MJPEG ----------

bool startCamera() {
  camera_config_t c = {};
  c.ledc_channel = LEDC_CHANNEL_0;
  c.ledc_timer = LEDC_TIMER_0;
  c.pin_d0 = CAM_Y2; c.pin_d1 = CAM_Y3; c.pin_d2 = CAM_Y4; c.pin_d3 = CAM_Y5;
  c.pin_d4 = CAM_Y6; c.pin_d5 = CAM_Y7; c.pin_d6 = CAM_Y8; c.pin_d7 = CAM_Y9;
  c.pin_xclk = CAM_XCLK; c.pin_pclk = CAM_PCLK; c.pin_vsync = CAM_VSYNC; c.pin_href = CAM_HREF;
  c.pin_sccb_sda = CAM_SIOD; c.pin_sccb_scl = CAM_SIOC;
  c.pin_pwdn = CAM_PWDN; c.pin_reset = CAM_RESET;
  c.xclk_freq_hz = 10000000;   // 10 MHz: mas estable que 20 MHz con la placa AI-Thinker
  c.pixel_format = PIXFORMAT_JPEG;
  c.frame_size = CAMERA_FRAME_SIZE;
  c.jpeg_quality = CAMERA_JPEG_QUALITY;
  c.grab_mode = CAMERA_GRAB_LATEST;   // siempre el cuadro mas reciente
  if (psramFound()) {
    c.fb_count = 2;
    c.fb_location = CAMERA_FB_IN_PSRAM;
  } else {
    c.fb_count = 1;
    c.fb_location = CAMERA_FB_IN_DRAM;
    c.frame_size = FRAMESIZE_QVGA;
  }
  esp_err_t err = esp_camera_init(&c);
  if (err != ESP_OK) {
    Serial.printf("[camara] ERROR al iniciar (0x%x): revisa el cable plano de la camara\n", err);
    return false;
  }
  Serial.println("[camara] lista");
  return true;
}

#ifndef CAMERA_MAX_CLIENTS
#define CAMERA_MAX_CLIENTS 3   // streams a la vez (edge + visores)
#endif

#define PART_BOUNDARY "123456789000000000000987654321"

// Copia el cuadro actual a `buf` (crece si hace falta) y suelta el de la camara enseguida,
// asi un cliente lento no le quita cuadros a los demas. Devuelve el largo o 0 si no hubo cuadro.
static size_t grabFrame(uint8_t*& buf, size_t& cap) {
  camera_fb_t* fb = esp_camera_fb_get();
  if (!fb) return 0;
  size_t len = fb->len;
  if (len > cap) {
    free(buf);
    cap = len + 4096;
    buf = (uint8_t*)(psramFound() ? ps_malloc(cap) : malloc(cap));
    if (!buf) { cap = 0; esp_camera_fb_return(fb); return 0; }
  }
  memcpy(buf, fb->buf, len);
  esp_camera_fb_return(fb);
  return len;
}

// Cada cliente del stream va en su propia tarea: el servidor sigue atendiendo a otros
// (antes la camara atendia a uno solo y el resto esperaba en fila, a veces para siempre).
static volatile int streamClients = 0;
static portMUX_TYPE streamMux = portMUX_INITIALIZER_UNLOCKED;

static void streamTask(void* arg) {
  httpd_req_t* req = (httpd_req_t*)arg;
  char part[64];
  uint8_t* buf = NULL;
  size_t cap = 0;
  unsigned long frames = 0, startMs = millis();
  int misses = 0;
  esp_err_t res = httpd_resp_set_type(req, "multipart/x-mixed-replace;boundary=" PART_BOUNDARY);
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  Serial.printf("[camara] cliente conectado al stream (%d en total)\n", streamClients);
  while (res == ESP_OK) {
    size_t len = grabFrame(buf, cap);
    if (!len) {
      // Sin cuadro: suele ser falta de energia. Se reintenta antes de cortar.
      Serial.printf("[camara] no llego el cuadro (%d seguidos)\n", ++misses);
      if (misses >= 10) break;
      delay(100);
      continue;
    }
    misses = 0;
    size_t hlen = snprintf(part, sizeof(part),
                           "Content-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n", len);
    res = httpd_resp_send_chunk(req, "\r\n--" PART_BOUNDARY "\r\n", strlen("\r\n--" PART_BOUNDARY "\r\n"));
    if (res == ESP_OK) res = httpd_resp_send_chunk(req, part, hlen);
    if (res == ESP_OK) res = httpd_resp_send_chunk(req, (const char*)buf, len);
    if (res != ESP_OK) {
      Serial.printf("[camara] fallo el envio de un cuadro de %u bytes (red o cliente cerrado)\n", len);
      break;
    }
    if (++frames % 100 == 0) {
      Serial.printf("[camara] %lu cuadros enviados, %.1f fps\n", frames,
                    frames * 1000.0f / (millis() - startMs));
    }
    delay(CAMERA_FRAME_DELAY_MS);   // limita los fps para no saturar el hotspot
  }
  free(buf);
  httpd_req_async_handler_complete(req);   // cierra la conexion
  portENTER_CRITICAL(&streamMux);
  streamClients--;
  portEXIT_CRITICAL(&streamMux);
  Serial.println("[camara] cliente desconectado del stream");
  vTaskDelete(NULL);
}

static esp_err_t streamHandler(httpd_req_t* req) {
  portENTER_CRITICAL(&streamMux);
  bool full = streamClients >= CAMERA_MAX_CLIENTS;
  if (!full) streamClients++;
  portEXIT_CRITICAL(&streamMux);
  if (full) {
    Serial.println("[camara] stream rechazado: ya hay demasiados clientes");
    httpd_resp_set_status(req, "503 Service Unavailable");
    return httpd_resp_send(req, "demasiados clientes", HTTPD_RESP_USE_STRLEN);
  }
  httpd_req_t* copy = NULL;
  if (httpd_req_async_handler_begin(req, &copy) != ESP_OK ||
      xTaskCreate(streamTask, "stream", 6144, copy, 5, NULL) != pdPASS) {
    if (copy) httpd_req_async_handler_complete(copy);
    portENTER_CRITICAL(&streamMux);
    streamClients--;
    portEXIT_CRITICAL(&streamMux);
    Serial.println("[camara] no se pudo iniciar el stream (memoria)");
    return ESP_FAIL;
  }
  return ESP_OK;
}

// Una foto suelta, para revisar la camara.
static esp_err_t captureHandler(httpd_req_t* req) {
  uint8_t* buf = NULL;
  size_t cap = 0;
  size_t len = grabFrame(buf, cap);
  if (!len) {
    free(buf);
    httpd_resp_send_500(req);
    return ESP_FAIL;
  }
  httpd_resp_set_type(req, "image/jpeg");
  httpd_resp_set_hdr(req, "Cache-Control", "no-store");
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  esp_err_t res = httpd_resp_send(req, (const char*)buf, len);
  free(buf);
  return res;
}

// Visor para el navegador: lee el mismo /stream continuo (una sola conexion, sin el costo de
// pedir foto por foto) y separa los cuadros en JavaScript. Si pasan 4 s sin un cuadro nuevo,
// corta y vuelve a conectar solo (un <img src=/stream> se queda congelado para siempre).
static const char VIEWER_HTML[] =
  "<!doctype html><html><head><meta charset=utf-8>"
  "<meta name=viewport content='width=device-width,initial-scale=1'>"
  "<title>Sentrya camara</title>"
  "<style>body{margin:0;background:#111;color:#ccc;font:14px sans-serif;text-align:center}"
  "img{max-width:100%;margin-top:8px}</style></head><body>"
  "<img id=v alt=camara><div id=s>conectando...</div><script>"
  "const v=document.getElementById('v'),s=document.getElementById('s');"
  "const en=new TextEncoder(),de=new TextDecoder(),K=en.encode('Content-Length: '),E=en.encode('\\r\\n\\r\\n');"
  "let n=0,t=Date.now();"
  "function find(b,p,f){f=f||0;o:for(let i=f;i<=b.length-p.length;i++){"
  "for(let j=0;j<p.length;j++)if(b[i+j]!==p[j])continue o;return i;}return -1;}"
  "function cat(a,b){const c=new Uint8Array(a.length+b.length);c.set(a);c.set(b,a.length);return c;}"
  "function show(j){const u=URL.createObjectURL(new Blob([j],{type:'image/jpeg'}));"
  "const o=v.src;v.src=u;if(o.startsWith('blob:'))URL.revokeObjectURL(o);"
  "if(++n%20==0){s.textContent=(20000/(Date.now()-t)).toFixed(1)+' fps';t=Date.now();}}"
  "async function go(){for(;;){const ac=new AbortController();let last=Date.now();"
  "const wd=setInterval(()=>{if(Date.now()-last>4000)ac.abort();},1000);"
  "try{const r=await fetch('/stream?'+Date.now(),{signal:ac.signal});const rd=r.body.getReader();"
  "let b=new Uint8Array(0);for(;;){const x=await rd.read();if(x.done)break;b=cat(b,x.value);"
  "for(;;){const h=find(b,K);if(h<0)break;const e=find(b,E,h);if(e<0)break;"
  "const len=parseInt(de.decode(b.subarray(h+K.length,e)));if(b.length<e+4+len)break;"
  "show(b.slice(e+4,e+4+len));b=b.slice(e+4+len);last=Date.now();}}}catch(x){}"
  "clearInterval(wd);s.textContent='reconectando...';await new Promise(r=>setTimeout(r,500));}}"
  "go();</script></body></html>";

static esp_err_t viewerHandler(httpd_req_t* req) {
  httpd_resp_set_type(req, "text/html");
  return httpd_resp_send(req, VIEWER_HTML, HTTPD_RESP_USE_STRLEN);
}

void startStreamServer() {
  httpd_config_t config = HTTPD_DEFAULT_CONFIG();
  config.server_port = 81;
  config.ctrl_port = 32769;
  // Limite corto (5 s): si la red se traba, el envio falla rapido, la conexion se cierra
  // y el cliente (el edge o el visor) vuelve a conectar enseguida.
  config.send_wait_timeout = 5;
  config.recv_wait_timeout = 5;
  config.max_open_sockets = CAMERA_MAX_CLIENTS + 3;   // los streams + visor, fotos y reconexiones
  httpd_uri_t stream = {};
  stream.uri = "/stream";
  stream.method = HTTP_GET;
  stream.handler = streamHandler;
  httpd_uri_t capture = {};
  capture.uri = "/capture";
  capture.method = HTTP_GET;
  capture.handler = captureHandler;
  httpd_uri_t viewer = {};
  viewer.uri = "/";
  viewer.method = HTTP_GET;
  viewer.handler = viewerHandler;
  if (httpd_start(&streamServer, &config) == ESP_OK) {
    httpd_register_uri_handler(streamServer, &stream);
    httpd_register_uri_handler(streamServer, &capture);
    httpd_register_uri_handler(streamServer, &viewer);
    Serial.printf("[camara] visor en http://%s:81/  (stream del edge en :81/stream)\n",
                  WiFi.localIP().toString().c_str());
  }
}

// ---------- Actuadores ----------

void sirenIdle() {
  ledcDetach(PIN_SIREN);
  pinMode(PIN_SIREN, OUTPUT);
  digitalWrite(PIN_SIREN, BUZZER_IDLE_HIGH ? HIGH : LOW);
  digitalWrite(PIN_FLASH, LOW);
}

void setSiren(bool on, const char* reason) {
  if (on && !sirenOn) {
    ledcAttach(PIN_SIREN, 2000, 8);
    sirenSinceMs = millis();
  }
  sirenOn = on;
  if (!on) sirenIdle();
  Serial.printf("[sirena] %s (%s)\n", on ? "ENCENDIDA" : "APAGADA", reason);
}

void updateSiren() {   // tono de dos frecuencias, alternando cada 300 ms
  if (!sirenOn) return;
  bool high = ((millis() - sirenSinceMs) / 300) % 2;
  static int lastFreq = 0;
  int freq = high ? 2600 : 1600;
  if (freq != lastFreq) {
    ledcWriteTone(PIN_SIREN, freq);
    if (SIREN_FLASH) digitalWrite(PIN_FLASH, high ? HIGH : LOW);
    lastFreq = freq;
  }
}

void writeLockRelay(bool energized) {
  if (!HAS_LOCK_RELAY) return;
  if (RELAY_ACTIVE_LOW && !energized) {
    // Modulo de 5 V activo en bajo: un HIGH de 3,3 V puede no soltarlo. Se suelta el pin.
    pinMode(PIN_LOCK_RELAY, INPUT);
  } else {
    pinMode(PIN_LOCK_RELAY, OUTPUT);
    digitalWrite(PIN_LOCK_RELAY, (energized != RELAY_ACTIVE_LOW) ? HIGH : LOW);
  }
}

void setLocked(bool lock, const char* reason) {
  locked = lock;
  writeLockRelay(!lock);   // rele en NO: energizada = desbloqueada
  Serial.printf("[cerradura] %s (%s)%s\n", lock ? "BLOQUEADA" : "DESBLOQUEADA", reason,
                HAS_LOCK_RELAY ? "" : " [simulada]");
}

// ---------- Cliente HTTP hacia el edge ----------

int postToEdge(const String& path, const String& body) {
  if (WiFi.status() != WL_CONNECTED) return -1;
  HTTPClient http;
  http.setConnectTimeout(1500);
  http.setTimeout(4000);         // por el hotspot, a veces el edge tarda mas de 2 s
  http.begin(String("http://") + edgeHost + ":" + edgePort + path);
  http.addHeader("Content-Type", "application/json");
  int code = http.POST(body);
  if (code >= 200 && code < 300) {
    lastEdgeOkMs = millis();
  } else if (code > 0) {
    Serial.printf("[edge] %s -> HTTP %d: %s\n", path.c_str(), code, http.getString().c_str());
  } else {
    Serial.printf("[edge] %s -> sin respuesta (%s)\n", path.c_str(), http.errorToString(code).c_str());
  }
  http.end();
  return code;
}

void sendSound() {
  JsonDocument doc;
  doc["node"] = NODE_ID;   // sin hora (no hay NTP): el edge usa la de llegada
  String body;
  serializeJson(doc, body);
  int code = postToEdge("/api/v1/security/sound", body);
  Serial.printf("[sonido] ruido fuerte -> HTTP %d\n", code);
}

void sendHeartbeat() {
  JsonDocument doc;
  doc["ip"] = WiFi.localIP().toString();
  doc["actuators"]["siren"] = sirenOn ? "on" : "off";
  doc["actuators"]["lock"] = locked ? "locked" : "unlocked";
  doc["uptime"] = millis() / 1000;
  String body;
  serializeJson(doc, body);
  int code = postToEdge(String("/api/v1/nodes/") + NODE_ID + "/heartbeat", body);
  Serial.printf("[heartbeat] ip %s senal %d dBm sirena %s cerradura %s camara %s -> HTTP %d\n",
                WiFi.localIP().toString().c_str(), WiFi.RSSI(), sirenOn ? "on" : "off",
                locked ? "bloqueada" : "desbloqueada", cameraOk ? "ok" : "ERROR", code);
}

// ---------- Servidor HTTP (ordenes del edge) ----------

void sendJson(int status, JsonDocument& doc) {
  String out;
  serializeJson(doc, out);
  server.send(status, "application/json", out);
}

void handleCommand() {
  JsonDocument req, res;
  if (deserializeJson(req, server.arg("plain"))) {
    res["error"] = "json invalido";
    sendJson(400, res);
    return;
  }
  const char* actuator = req["actuator"] | "";
  const char* action = req["action"] | "";
  JsonVariant params = req["params"];
  bool ok = false;
  bool simulated = false;

  if (strcmp(actuator, "siren") == 0 &&
      (strcmp(action, "on") == 0 || strcmp(action, "off") == 0)) {
    setSiren(strcmp(action, "on") == 0, "orden del edge");
    ok = true;
  } else if (strcmp(actuator, "lock") == 0 && strcmp(action, "lock") == 0) {
    unlockUntilMs = 0;
    setLocked(true, "orden del edge");
    ok = true; simulated = !HAS_LOCK_RELAY;
  } else if (strcmp(actuator, "lock") == 0 && strcmp(action, "unlock") == 0) {
    float seconds = params["pulseSeconds"] | (LOCK_PULSE_MS / 1000.0f);
    unlockUntilMs = millis() + (unsigned long)(seconds * 1000);
    if (unlockUntilMs == 0) unlockUntilMs = 1;
    setLocked(false, "orden del edge (pulso)");
    ok = true; simulated = !HAS_LOCK_RELAY;
  }
  if (!ok) Serial.printf("[orden] rechazada: %s %s\n", actuator, action);
  lastEdgeOkMs = millis();
  res["commandId"] = req["commandId"];
  res["status"] = ok ? "APPLIED" : "FAILED";
  res["simulated"] = simulated;
  sendJson(200, res);
}

void handleRoot() {
  JsonDocument doc;
  doc["node"] = NODE_ID;
  doc["siren"] = sirenOn ? "on" : "off";
  doc["lock"] = locked ? "locked" : "unlocked";
  doc["camera"] = cameraOk ? "ok" : "error";
  doc["stream"] = String("http://") + WiFi.localIP().toString() + ":81/stream";
  doc["uptime"] = millis() / 1000;
  doc["edge"] = edgeHost + ":" + edgePort;
  sendJson(200, doc);
}

// ---------- Sensor de sonido ----------

void IRAM_ATTR onSound() {
  soundFlag = true;
}

void checkSound() {
  if (!soundFlag) return;
  soundFlag = false;
  if (millis() - lastSoundMs < SOUND_DEBOUNCE_MS) return;
  lastSoundMs = millis();
  sendSound();
}

// ---------- Descubrimiento del edge ----------

void checkDiscovery() {
  int size = discovery.parsePacket();
  if (size <= 0) return;
  char buf[128];
  int len = discovery.read(buf, sizeof(buf) - 1);
  buf[len > 0 ? len : 0] = '\0';
  JsonDocument doc;
  if (deserializeJson(doc, buf) || strcmp(doc["service"] | "", "sentrya-edge") != 0) return;
  String host = discovery.remoteIP().toString();
  int port = doc["port"] | EDGE_PORT;
  if (host != edgeHost || port != edgePort) {
    Serial.printf("[descubrimiento] edge encontrado en %s:%d (antes %s:%d)\n",
                  host.c_str(), port, edgeHost.c_str(), edgePort);
    edgeHost = host;
    edgePort = port;
    lastHeartbeatMs = millis() - HEARTBEAT_INTERVAL_MS;   // avisarle enseguida nuestra IP
  }
}

// ---------- Wi-Fi ----------

void connectWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);          // responde mas rapido y el stream va mas fluido
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.printf("[wifi] conectando a %s", WIFI_SSID);
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 20000) {
    delay(500);
    Serial.print(".");
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("\n[wifi] conectado. IP del nodo: %s\n", WiFi.localIP().toString().c_str());
  } else {
    Serial.println("\n[wifi] no conecta todavia; se sigue reintentando en segundo plano");
  }
}

// ---------- Arranque y bucle ----------

void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println("\n=== Sentrya " NODE_ID " (Seguridad) ===");

  pinMode(PIN_FLASH, OUTPUT);
  digitalWrite(PIN_FLASH, LOW);
  sirenIdle();
  setLocked(true, "arranque");          // nunca arranca desbloqueada

  pinMode(PIN_SOUND, INPUT);
  attachInterrupt(digitalPinToInterrupt(PIN_SOUND), onSound, SOUND_ACTIVE_HIGH ? RISING : FALLING);

  cameraOk = startCamera();
  connectWiFi();

  server.on("/command", HTTP_POST, handleCommand);
  server.on("/", HTTP_GET, handleRoot);
  server.begin();
  if (cameraOk) startStreamServer();
  discovery.begin(DISCOVERY_PORT);
  Serial.println("[http] escuchando POST /command en el puerto 80");

  lastEdgeOkMs = millis();
  sendHeartbeat();
  lastHeartbeatMs = millis();
}

void loop() {
  server.handleClient();
  checkDiscovery();
  checkSound();
  updateSiren();

  if (!locked && unlockUntilMs && (long)(millis() - unlockUntilMs) >= 0) {
    unlockUntilMs = 0;
    setLocked(true, "fin del pulso");
  }
  if (millis() - lastHeartbeatMs >= HEARTBEAT_INTERVAL_MS) {
    lastHeartbeatMs = millis();
    sendHeartbeat();
  }
  if (sirenOn && (millis() - lastEdgeOkMs > EDGE_LOST_OFF_MS ||
                  millis() - sirenSinceMs > SIREN_MAX_MS)) {
    setSiren(false, millis() - sirenSinceMs > SIREN_MAX_MS ? "tope local" : "sin contacto con el edge");
  }
}
