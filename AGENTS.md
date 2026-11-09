# AGENTS.md — Sentrya · IoT Edge Service

## 1. Contexto

**Sentrya** es el producto de la startup **SpaceUp** (curso 1ASI0572 Desarrollo de Soluciones IoT, UPC, ciclo 202620). Es una plataforma de monitoreo preventivo de seguridad y condiciones ambientales del hogar.

**Este repo contiene SOLO el IoT Edge Service.** Corre en una **laptop con Ubuntu** dentro de la red Wi‑Fi de la maqueta, que hace las veces de dispositivo edge. La maqueta es una casa de 40 × 30 cm con **hardware real**.

Principios (cap. 5.6 del informe):
- Procesamiento en el borde y baja latencia.
- **Los actuadores se accionan localmente, sin esperar a la nube.**
- Si la nube no está disponible, todo sigue funcionando y se sincroniza después.
- La imagen se procesa en el edge y solo se envían eventos.
- Prevención de falsos positivos mediante correlación, lecturas consecutivas e histéresis.

## 2. Arquitectura

```
ESP32 x3 (C++) ──HTTP/JSON (red local)──► EDGE (Python/Flask, laptop Ubuntu) ── SQLite
      ▲                                      │  ▲
      └──── HTTP /command (órdenes) ─────────┘  │ MQTT
                                                 ▼
                                   Mosquitto ⇄ IoT Monitoring Service (Go)
```

- Las ESP32 envían lecturas y eventos al edge, y reciben órdenes.
- El edge publica en el broker. El servicio de IoT Monitoring se suscribe, registra las lecturas y genera alertas.
- El edge **no** envía notificaciones ni llama al API Gateway. Notificar al usuario y escalar es tarea del backend; el edge solo publica.

## 3. Hardware (maqueta)

| Nodo | Placa | Sensores | Actuadores |
|---|---|---|---|
| 1 · Seguridad | ESP32‑CAM | Cámara (stream MJPEG), sensor de sonido **SA92 (salida digital: solo detecta ruido fuerte, no mide dB)** | Buzzer pasivo como sirena (PWM), cerradura solenoide 12 V con relé |
| 2 · Humedad | ESP32 DevKit | DHT22 (mínimo 2 s entre lecturas) | Humidificador ultrasónico 5 V con relé (on/off) |
| 3 · Ambiental | ESP32 DevKit | Sensor de temperatura (modelo por confirmar; intervalo mínimo configurable) | Ventilador 40 mm con MOSFET (PWM 0–100 %), buzzer, válvula solenoide 12 V con relé (**solo control remoto**) |

- No hay aire acondicionado, subsistema eléctrico ni sensor de fuga.
- Algunos relés, el MOSFET y las fuentes pueden no estar disponibles. **Al edge no le importa:** envía la orden igual, y la ESP32 la ejecuta o la simula (LED o log), respondiendo `simulated: true`.

## 4. Stack

- Python 3.11+ con Flask (escuchando en `0.0.0.0`). Cliente MQTT `paho-mqtt` 2.x, con `CallbackAPIVersion.VERSION2`.
- SQLite mediante SQLAlchemy o Peewee.
- OpenCV (`cv2.VideoCapture("http://<ip-esp32cam>:81/stream")`) para leer la cámara.
- Detección de persona y reconocimiento facial detrás de interfaces. Implementaciones sugeridas: un detector ligero de personas (por ejemplo, YOLOv8n o MobileNet‑SSD con OpenCV DNN) y reconocimiento con `face_recognition` u OpenCV YuNet+SFace. Debe existir siempre una implementación **simulada**.
- `requests` para enviar órdenes a las ESP32.
- Pruebas con pytest, con un reloj inyectable.
- Configuración por `.env` y `config/devices.yaml`.

## 5. Componentes (deben coincidir con el diagrama 5.6.2)

| Componente | Responsabilidad |
|---|---|
| **Sensor Manager** | API Flask para las ESP32. Valida, normaliza, persiste y distribuye a los servicios |
| **Person Detection Service** | Lee el stream y emite eventos de persona detectada y reconocida, con su confianza |
| **Sound Analysis Service** | Procesa los disparos digitales del SA92 y emite sonido sospechoso |
| **Rules Engine** | Máquinas de estado de seguridad, temperatura y humedad. Devuelve acciones y eventos |
| **Local Safety Controller** | **Único** componente que envía órdenes a los actuadores. Lleva la cola de órdenes y los acuses |
| **IoT Communication Client** | MQTT: publicación con QoS 1, store-and-forward, suscripción a órdenes |

```
edge/
├── app.py                      arranque (Flask + hilos MQTT/cámara)
├── sensors/                    sensor_manager.py, api.py, normalizers.py
├── security/                   person_detection.py, sound_analysis.py, rules.py
├── temperature/rules.py
├── humidity/rules.py
├── actuation/                  local_safety_controller.py, esp32_client.py
├── communication/              iot_client.py, outbox.py
├── config/                     settings.py, thresholds.py, devices.yaml, profiles/{report,demo}.yaml
├── shared/                     db.py, models.py, clock.py, logging.py
├── data/                       edge.db, evidence/, known_faces/   (ignorado por git)
└── tests/
```

Cada módulo de reglas expone la misma interfaz:
```
evaluate(input, state, clock) -> (new_state, actions[], events[])
```
Agregar un subsistema no debe tocar el núcleo.

## 6. Umbrales y perfiles

Los umbrales **nunca se escriben en el código**. Se cargan de `config/profiles/<THRESHOLD_PROFILE>.yaml` y se guardan en SQLite. El informe dice que son configurables y deben calibrarse.

| Parámetro | `report` (informe) | `demo` (maqueta) |
|---|---|---|
| T1 Warning | 45 °C | 28 °C |
| T2 Critical | 57 °C | 32 °C |
| Histéresis de temperatura | 2 °C | 1 °C |
| Humedad: encender por debajo de | 40 % HR | Calibrar con la humedad de la sala |
| Humedad: apagar al llegar a | 50 % HR | 55 % HR (maqueta) |
| Humedad alta | > 60 % HR | Calibrar |
| Lecturas consecutivas para confirmar | 3 | 3 |
| Ventana de correlación de seguridad | 10 s | 10 s |
| Ventana de validación del usuario | 30 s | 30 s |
| Confianza de persona / reconocimiento | 0.70 / 0.80 | 0.70 / 0.80 |

Los valores del perfil demo son orientativos; se ajustan en el archivo. Un endpoint de simulación permite inyectar lecturas si un sensor falla durante la demo (§8).

## 7. Reglas

### Temperatura (Nodo 3)
| Estado | Entrada | Acciones | Muestreo pedido a la ESP32 |
|---|---|---|---|
| NORMAL | < T1 − histéresis | Ventilador apagado, buzzer apagado | 30 s |
| WARNING | ≥ T1 | Ventilador al 50 % | 5 s |
| CRITICAL | **3 lecturas consecutivas ≥ T2** | Ventilador al 100 %, buzzer encendido, publicar escalamiento | 1 s, o el mínimo del sensor si es mayor |

- Una lectura ≥ T2 sin confirmar mantiene el estado en WARNING.
- Para bajar de estado: CRITICAL pasa a WARNING por debajo de T2 − histéresis; WARNING pasa a NORMAL por debajo de T1 − histéresis.
- Opcional: un aumento ≥ 8 °C/min lleva a CRITICAL (desactivado por defecto).
- No hay A/C (es una desviación del informe).

### Humedad (Nodo 2; lectura cada 10 s; telemetría cada 60 s con la última lectura)
- 3 lecturas consecutivas por debajo del umbral de encendido: estado LOW, humidificador encendido.
- Al llegar al umbral de apagado: humidificador apagado. Si está dentro del rango, el estado pasa a NORMAL.
- 3 lecturas consecutivas por encima del umbral alto: estado HIGH, humidificador apagado.
- **Sensor caído** (sin lecturas durante 3 × el intervalo, o el DHT22 devuelve error): humidificador apagado y evento `device_error`.
- **Modo AUTO / MANUAL:** en MANUAL las reglas no encienden el humidificador, pero sí lo apagan por sensor caído o humedad alta.
- Cada cambio de estado se publica de inmediato, sin esperar la telemetría de 60 s.

### Seguridad (Nodo 1)
- **Persona desconocida:** persona detectada con confianza ≥ 0.70 que no coincide con ningún rostro conocido (coincidencia < 0.80, o no se ve el rostro). Los rostros conocidos son fotos en `data/known_faces/<nombre>/`.
- **Sonido sospechoso:** el SA92 es digital, así que la regla es **N disparos dentro de una ventana de M segundos** (configurable; por defecto N = 1). La ESP32 aplica el debounce y el edge cuenta. El criterio de dB y clasificación de 5.6.3 **no se implementa** por limitación del hardware (es una desviación del informe).
- **Intrusión:** persona desconocida y sonido sospechoso con 10 s o menos de diferencia, en cualquier orden.
  1. Sirena encendida, cerradura bloqueada y foto guardada en `data/evidence/`. Todo local e inmediato.
  2. Se publica la intrusión.
  3. Se abre la ventana de validación de 30 s.
     - Si el usuario confirma, o no responde en 30 s: se publica el escalamiento.
     - Si el usuario rechaza: sirena apagada, estado MONITORING.
  4. Mientras está abierta la ventana, no se dispara otra intrusión.
- La validación llega por MQTT (§9) o por el endpoint local de demo (§8).
- **La cerradura nunca se desbloquea por una regla local.** Solo por una orden explícita (remota o del endpoint de demo).
- **Semántica de la cerradura:** configurable con `LOCK_ENERGIZE_TO_UNLOCK` (por defecto `true`: sin energía está bloqueada). En ese caso, bloquear = relé apagado y desbloquear = pulso de `LOCK_PULSE_SECONDS`. Hay que comprobarlo con el hardware real.

### Válvula (Nodo 3)
- Sin regla local. Solo responde a órdenes de abrir o cerrar.

## 8. API local para las ESP32 (Flask, puerto 5000)

**Contrato propuesto: confirmar con quien programa las ESP32.**

| Método | Ruta | Body / uso |
|---|---|---|
| POST | /api/v1/readings | `{ "node": "node-3", "deviceKey": "temperature", "metricType": "temperature", "value": 26.4, "unit": "C", "measuredAt": "ISO-8601" }`. Si la ESP32 no tiene hora, el edge pone su propia marca de tiempo |
| POST | /api/v1/security/sound | `{ "node": "node-1", "triggeredAt": "ISO-8601" }` (un disparo del SA92) |
| POST | /api/v1/nodes/{node}/heartbeat | `{ "ip": "192.168.x.x", "actuators": { "humidifier": "off", ... }, "uptime": 123 }`. El edge guarda la IP para enviarle órdenes |
| POST | /api/v1/simulate/readings | Inyecta lecturas falsas (solo si `ENABLE_SIMULATION=true`) |
| POST | /api/v1/demo/security/validation | `{ "decision": "confirm" \| "reject" }`: simula la respuesta del usuario en la app |
| POST | /api/v1/demo/commands | `{ "deviceKey": "valve", "action": "open" }`: orden manual para la demo |
| GET | /api/v1/status | Estado de los subsistemas, actuadores, cola pendiente y conexión MQTT |

### Órdenes del edge a la ESP32
`POST http://<ip-nodo>/command`

Body:
```json
{ "commandId": "uuid", "actuator": "fan", "action": "set", "params": { "duty": 50 } }
```
Respuesta:
```json
{ "commandId": "...", "status": "APPLIED" | "FAILED", "simulated": false }
```

| Actuador | Acciones |
|---|---|
| `siren` | `on` / `off` |
| `lock` | `lock` / `unlock` |
| `humidifier` | `on` / `off` |
| `fan` | `set` con `duty` 0–100 |
| `buzzer` | `on` / `off` |
| `valve` | `open` / `close` |
| `sampling` | `set` con `intervalSeconds` |

- Timeout de 2 s, con hasta 3 reintentos.
- Si no hay respuesta, la orden queda en FAILED y se registra. **La acción local se considera ejecutada para la regla**, y se reintenta en el siguiente heartbeat.

## 9. Contrato MQTT con IoT Monitoring (Go)

El servicio de IoT Monitoring se suscribe a **`sentrya/+/+/readings`** y espera este payload:
```json
{"deviceId":"<string>","metricType":"temperature","value":38.2,"unit":"C"}
```

- El edge publica en **`sentrya/{SPACE_ID}/{deviceId}/readings`**, con QoS 1, y puede agregar campos extra que Go ignora: `readingId` (uuid), `measuredAt` y `localState`.
- **`deviceId` es el id que asigna IoT Monitoring** al registrar el dispositivo. Es un string opaco y se configura en `devices.yaml`.
- Si el dispositivo no existe o está inactivo en la nube, la lectura se rechaza. Por eso se registra y activa antes de usarlo, con los mismos umbrales del perfil activo.

### `config/devices.yaml`
```yaml
- key: temperature   # nombre lógico usado en el edge
  node: node-3
  deviceId: "<id de IoT>"
  metricType: temperature
  unit: C
```

### Qué se publica
| Lectura o evento | `metricType` | `value` | `unit` |
|---|---|---|---|
| Temperatura (cada lectura) | `temperature` | °C | `C` |
| Humedad (cada 60 s y en cada cambio de estado) | `humidity` | % HR | `%` |
| Intrusión detectada | `intrusion` | `1` | `event` |
| Escalamiento (intrusión confirmada o sin respuesta, temperatura CRITICAL) | `escalation` | `1` | `event` (+ `reason`) |
| Sensor caído | `device_error` | `1` | `event` |

Las intrusiones, escalamientos y errores de dispositivo se publican como "lecturas evento" porque el backend aún no acepta otro tipo de eventos. Se registran en la nube con un umbral máximo de 0, para que el valor 1 genere una alerta. La referencia a la evidencia va como campo extra `evidenceRef`.

### Nube → edge (propuesto; IoT Monitoring todavía NO lo implementa)
| Tópico | Contenido |
|---|---|
| `sentrya/{SPACE_ID}/{deviceId}/commands` | `{ "commandId", "action", "params" }` (válvula, cerradura, modo del humidificador) |
| `sentrya/{SPACE_ID}/security/validation` | `{ "eventId", "decision": "confirm" \| "reject" }` |
| `sentrya/{SPACE_ID}/config` (retained) | Umbrales |

El edge se suscribe desde ya. Mientras la nube no los publique, se usan los endpoints `/demo/*`.

### Conexión
- `MQTT_BROKER_URL`, usuario y contraseña opcionales, TLS opcional (`MQTT_TLS=true`).
- Reconexión automática.
- **Outbox:** todo lo publicable se guarda primero en SQLite con `synced = 0`. Un hilo lo envía en orden y lo marca al recibir el PUBACK.

## 10. Persistencia local (SQLite)

| Tabla | Columnas |
|---|---|
| `nodes` | `node`, `ip`, `last_seen_at`, `actuators_json` |
| `readings` | `id`, `device_key`, `metric_type`, `value`, `unit`, `measured_at`, `local_state` |
| `outbox` | `id` (readingId), `topic`, `payload_json`, `created_at`, `synced`, `attempts` |
| `subsystem_state` | `subsystem`, `state`, `counters_json`, `updated_at` (permite recuperar el estado tras un reinicio) |
| `commands` | `command_id`, `node`, `actuator`, `action`, `params_json`, `status`, `simulated`, `created_at`, `acked_at` |
| `security_events` | `event_id`, `type`, `evidence_path`, `validation`, `opened_at`, `closed_at` |

Retención: purgar lo sincronizado con más de `RETENTION_DAYS` días (por defecto 7).

## 11. Configuración (`.env`)

```
SPACE_ID=1
THRESHOLD_PROFILE=demo            # report | demo
FLASK_HOST=0.0.0.0
FLASK_PORT=5000
MQTT_BROKER_URL=tcp://localhost:1883
MQTT_USERNAME=
MQTT_PASSWORD=
MQTT_TLS=false
CAMERA_STREAM_URL=http://192.168.1.50:81/stream
PERSON_DETECTOR=mock              # mock | real
FACE_RECOGNIZER=mock              # mock | real
SOUND_TRIGGERS_REQUIRED=1
SOUND_WINDOW_SECONDS=3
LOCK_ENERGIZE_TO_UNLOCK=true
LOCK_PULSE_SECONDS=3
ENABLE_SIMULATION=true
RETENTION_DAYS=7
```

## 12. Reglas para el agente

- **Primero se acciona el actuador.** Persistir y publicar viene después y nunca bloquea una acción local.
- Ninguna lectura ni evento se pierde por caída del broker (outbox).
- Las máquinas de estado son puras y deterministas, con reloj inyectable. Pruebas obligatorias:
  - histéresis de temperatura;
  - confirmación por 3 lecturas;
  - encendido y apagado del humidificador;
  - correlación de 10 s en cualquier orden;
  - timeout de 30 s;
  - modo MANUAL;
  - sensor caído.
- Las ventanas de tiempo usan un reloj monótono. Las marcas de tiempo, UTC ISO‑8601.
- Si llega una orden para un nodo sin IP conocida o que no responde: FAILED, se registra, no se inventa un estado.
- Los modelos de visión cargan una sola vez y corren en su propio hilo. Si la cámara no está disponible, el subsistema de seguridad sigue vivo y se reporta como degradado en `/status`.
- Nada de secretos ni datos de `data/` en git.
- Mantener los nombres de componentes del diagrama 5.6.2. Registrar las desviaciones en `docs/desviaciones.md`.

## 13. Desviaciones respecto del informe (documentar en él)

- El edge es una laptop con Ubuntu, no una Raspberry Pi.
- Sonido: el SA92 es digital, así que se usa un conteo de disparos en lugar de dB y clasificación de sonidos.
- No hay A/C: CRITICAL solo activa el ventilador al 100 % y el buzzer.
- La sirena es un buzzer pasivo.
- La válvula es solo remota; no hay sensor de fuga.
- La demo usa umbrales reducidos (perfil `demo`).
- Se agrega la orden de muestreo variable a la ESP32.
- Los eventos se publican como "lecturas evento".

## 14. Coordinación pendiente (fuera de este repo)

- **Con quien programa las ESP32:**
  - endpoints de §8 (envío de lecturas, sonido y heartbeat);
  - servidor `/command` en cada nodo;
  - debounce del SA92;
  - stream de la ESP32‑CAM en el puerto 81;
  - respuesta `simulated` cuando falta el actuador;
  - comprobar la lógica de la cerradura.
- **Con quien hace IoT Monitoring:**
  - agregar los `metricType` `humidity`, `intrusion`, `escalation` y `device_error` (hoy asume `temperature`, `voltage`, `sound`);
  - registrar los dispositivos con los umbrales del perfil activo;
  - implementar los tópicos de nube hacia edge (`commands`, `security/validation`, `config`);
  - decidir la autenticación de dispositivos en el broker.

## 15. Comandos

```
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
flask --app app run --host 0.0.0.0 --port 5000
pytest

# Broker local para desarrollo (Mosquitto 2 necesita listener explícito)
# mosquitto.conf:  listener 1883  /  allow_anonymous true
docker run -p 1883:1883 -v $PWD/mosquitto.conf:/mosquitto/config/mosquitto.conf eclipse-mosquitto
sudo ufw allow 5000/tcp
```

## Agent skills

### Issue tracker

Los issues se llevan en GitHub Issues (CLI `gh`). See `docs/agents/issue-tracker.md`.

### Triage labels

Etiquetas canónicas por defecto: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: un `GLOSSARY.md` y `docs/adr/` en la raíz. See `docs/agents/domain.md`.
