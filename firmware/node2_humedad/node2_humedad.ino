// Sentrya - Nodo 2 (Humedad). ESP32 DevKit V1 + DHT22 + rele del humidificador.
//
// - Cada READ_INTERVAL_MS lee el DHT22 y hace POST /api/v1/readings al edge.
//   Si el DHT22 falla, envia la lectura con "error": "dht22" (sin value ni unit).
// - Cada HEARTBEAT_INTERVAL_MS hace POST /api/v1/nodes/node-2/heartbeat con su IP
//   y el estado del humidificador; el edge lo usa para enviarle ordenes y reconciliar.
// - Atiende POST /command (puerto 80): { "commandId", "actuator": "humidifier", "action": "on"|"off" }
//   y responde { "commandId", "status": "APPLIED"|"FAILED", "simulated": bool }.
// - GET / devuelve el estado del nodo (para probar desde el navegador).
// - Escucha el anuncio UDP del edge (puerto DISCOVERY_PORT) y toma su IP de ahi: si la IP de
//   la laptop cambia, no hay que volver a subir el firmware. EDGE_HOST queda como respaldo.
//
// Las reglas (cuando encender o apagar) viven en el edge. Este firmware solo mide y obedece,
// salvo una proteccion: si pierde contacto con el edge, apaga el humidificador.
//
// Bibliotecas (Gestor de bibliotecas): "DHT sensor library" (Adafruit, instala tambien
// "Adafruit Unified Sensor") y "ArduinoJson" (v7). Placa: "ESP32 Dev Module".

#include <WiFi.h>
#include <WebServer.h>
#include <HTTPClient.h>
#include <WiFiUdp.h>
#include <ArduinoJson.h>
#include <DHT.h>
#include "config.h"

WebServer server(80);
DHT dht(PIN_DHT, DHT22);
WiFiUDP discovery;

String edgeHost = EDGE_HOST;   // se actualiza con el anuncio del edge
int edgePort = EDGE_PORT;

bool humidifierOn = false;
float lastHumidity = NAN;
unsigned long lastReadMs = 0;
unsigned long lastHeartbeatMs = 0;
unsigned long lastEdgeOkMs = 0;

// ---------- Actuador ----------

void setHumidifier(bool on, const char* reason) {
  humidifierOn = on;
  if (HAS_HUMIDIFIER_RELAY) {
    if (RELAY_ACTIVE_LOW && !on) {
      // Modulo de 5 V activo en bajo: un HIGH de 3,3 V no alcanza para apagarlo.
      // Para apagar se suelta el pin (entrada) y el propio modulo lleva IN a su VCC.
      pinMode(PIN_RELAY, INPUT);
    } else {
      pinMode(PIN_RELAY, OUTPUT);
      digitalWrite(PIN_RELAY, (on != RELAY_ACTIVE_LOW) ? HIGH : LOW);
    }
  }
  digitalWrite(PIN_LED, on ? HIGH : LOW);
  Serial.printf("[humidificador] %s (%s)%s\n", on ? "ENCENDIDO" : "APAGADO", reason,
                HAS_HUMIDIFIER_RELAY ? "" : " [simulado con LED]");
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

void sendReading() {
  float h = dht.readHumidity();
  float t = dht.readTemperature();
  JsonDocument doc;
  doc["node"] = NODE_ID;
  doc["deviceKey"] = "humidity";
  doc["metricType"] = "humidity";
  if (isnan(h)) {
    doc["error"] = "dht22";
  } else {
    lastHumidity = h;
    doc["value"] = roundf(h * 10) / 10.0;
    doc["unit"] = "%";
  }
  String body;
  serializeJson(doc, body);
  int code = postToEdge("/api/v1/readings", body);
  if (isnan(h)) Serial.printf("[dht22] ERROR de lectura -> HTTP %d\n", code);
  else Serial.printf("[dht22] humedad %.1f %%  temp %.1f C -> HTTP %d\n", h, t, code);
}

void sendHeartbeat() {
  JsonDocument doc;
  doc["ip"] = WiFi.localIP().toString();
  doc["actuators"]["humidifier"] = humidifierOn ? "on" : "off";
  doc["uptime"] = millis() / 1000;
  String body;
  serializeJson(doc, body);
  int code = postToEdge(String("/api/v1/nodes/") + NODE_ID + "/heartbeat", body);
  Serial.printf("[heartbeat] ip %s humidificador %s -> HTTP %d\n",
                WiFi.localIP().toString().c_str(), humidifierOn ? "on" : "off", code);
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
  bool ok = false;
  if (strcmp(actuator, "humidifier") == 0) {
    if (strcmp(action, "on") == 0)  { setHumidifier(true, "orden del edge");  ok = true; }
    if (strcmp(action, "off") == 0) { setHumidifier(false, "orden del edge"); ok = true; }
  }
  if (!ok) Serial.printf("[orden] rechazada: %s %s\n", actuator, action);
  lastEdgeOkMs = millis();
  res["commandId"] = req["commandId"];
  res["status"] = ok ? "APPLIED" : "FAILED";
  res["simulated"] = !HAS_HUMIDIFIER_RELAY;
  sendJson(200, res);
}

void handleRoot() {
  JsonDocument doc;
  doc["node"] = NODE_ID;
  doc["humidifier"] = humidifierOn ? "on" : "off";
  if (isnan(lastHumidity)) doc["humidity"] = nullptr; else doc["humidity"] = lastHumidity;
  doc["relay"] = HAS_HUMIDIFIER_RELAY;
  doc["uptime"] = millis() / 1000;
  doc["edge"] = edgeHost + ":" + edgePort;
  sendJson(200, doc);
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
  WiFi.setSleep(false);          // responde mas rapido a /command
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
  Serial.println("\n=== Sentrya " NODE_ID " (Humedad) ===");

  pinMode(PIN_LED, OUTPUT);
  setHumidifier(false, "arranque");   // el edge lo vuelve a encender si hace falta

  dht.begin();
  connectWiFi();

  server.on("/command", HTTP_POST, handleCommand);
  server.on("/", HTTP_GET, handleRoot);
  server.begin();
  discovery.begin(DISCOVERY_PORT);
  Serial.println("[http] escuchando POST /command en el puerto 80");

  delay(2000);                         // el DHT22 necesita ~2 s tras encender
  lastEdgeOkMs = millis();
  sendHeartbeat();
  sendReading();
  lastHeartbeatMs = lastReadMs = millis();
}

void loop() {
  server.handleClient();
  checkDiscovery();
  unsigned long now = millis();

  if (now - lastReadMs >= READ_INTERVAL_MS) {
    lastReadMs = now;
    sendReading();
  }
  if (now - lastHeartbeatMs >= HEARTBEAT_INTERVAL_MS) {
    lastHeartbeatMs = now;
    sendHeartbeat();
  }
  // millis() fresco: sendReading/sendHeartbeat pueden haber movido lastEdgeOkMs despues de `now`
  if (humidifierOn && millis() - lastEdgeOkMs > EDGE_LOST_OFF_MS) {
    setHumidifier(false, "sin contacto con el edge");
  }
}
