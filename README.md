# Sentrya · IoT Edge Service

Servicio edge de **Sentrya** (SpaceUp, curso 1ASI0572, UPC). Corre en una laptop con Ubuntu dentro de la red Wi‑Fi de la maqueta. Recibe lo que miden las tres ESP32, decide localmente qué hacer con los actuadores sin esperar a la nube, y publica lecturas y eventos por MQTT para que IoT Monitoring genere las alertas.

La especificación completa está en [`AGENTS.md`](AGENTS.md). Las decisiones de diseño están en [`docs/adr/`](docs/adr/), las desviaciones del informe en [`docs/desviaciones.md`](docs/desviaciones.md) y lo que falta coordinar con otros equipos en [`docs/pendientes.md`](docs/pendientes.md).

## Cómo encaja

```
ESP32 x3 ──HTTP (red local)──►  EDGE (Flask, puerto 5000) ──► SQLite (archivo edge/data/edge.db)
   ▲                               │
   └──── POST /command ────────────┤
                                   │ MQTT, QoS 1, outbox store-and-forward
                                   ▼
                     Mosquitto (broker, puerto 1883) ⇄ IoT Monitoring (Go)
```

- **Base de datos:** SQLite, un archivo local (`edge/data/edge.db`) que el edge crea solo al arrancar. **No usa Docker ni un servidor de base de datos.**
- **Envío a la nube:** todo sale por **MQTT** hacia un broker **Mosquitto**. El edge publica en `sentrya/{SPACE_ID}/{deviceId}/readings`. Antes de publicar, cada mensaje se guarda en el outbox de SQLite, así que si el broker se cae nada se pierde y se envía al volver.
- **Docker** solo se usa para levantar Mosquitto en desarrollo y en la demo (`docker-compose.yml`). En la demo real el broker puede ser el que use IoT Monitoring; basta con apuntar `MQTT_BROKER_URL` a él.
- El edge **no** manda notificaciones ni llama al API Gateway: solo publica.

## Requisitos

- Python **3.11 o superior**.
- Docker con Docker Compose, para el broker local. Si ya tienes un Mosquitto instalado, no hace falta.
- `curl` para las pruebas manuales.

## Instalación

En Ubuntu (la laptop de la maqueta):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt     # incluye requirements.txt y pytest
cp .env.example .env
```

En Windows con Git Bash (desarrollo):

```bash
python -m venv .venv
# PyYAML no tiene wheel para Python 3.15 en Windows; con 3.11-3.13 no hace falta esta línea
PYYAML_FORCE_CYTHON=0 PYYAML_FORCE_LIBYAML=0 .venv/Scripts/python -m pip install pyyaml
.venv/Scripts/python -m pip install -r requirements-dev.txt
cp .env.example .env
```

En Windows, reemplaza `python` por `.venv/Scripts/python` en los comandos de abajo.

## Configuración

Todo se configura en `.env`. Las variables están documentadas en `.env.example` y en `AGENTS.md` §11. Las más importantes:

| Variable | Para qué |
|---|---|
| `THRESHOLD_PROFILE` | `demo` (umbrales reducidos para la maqueta) o `report` (los del informe) |
| `MQTT_BROKER_URL` | Broker MQTT, por ejemplo `tcp://localhost:1883` |
| `SPACE_ID` | Parte del tópico MQTT |
| `ENABLE_SIMULATION` | Habilita los endpoints `/simulate/*` para la demo |
| `PERSON_DETECTOR` / `FACE_RECOGNIZER` | `mock` (por defecto) o `real` (OpenCV, ver [`docs/vision.md`](docs/vision.md)) |
| `CAMERA_STREAM_URL` | Stream MJPEG de la ESP32‑CAM, solo con visión real |

Además:

- **Umbrales:** `edge/config/profiles/demo.yaml` y `report.yaml`. El YAML manda en cada arranque, así que para calibrar basta con editarlo y reiniciar.
- **Dispositivos:** `edge/config/devices.yaml` mapea cada dispositivo lógico al `deviceId` que asigna IoT Monitoring. Reemplaza los `REEMPLAZAR-ID-...` por los ids reales cuando los registren en la nube.

## Ejecutar

```bash
docker compose up -d        # 1. broker Mosquitto en el puerto 1883
python -m edge.app          # 2. edge en http://0.0.0.0:5000
```

Al arrancar, la consola muestra la retención aplicada y la conexión MQTT. Desde ahí, cada transición de estado y cada orden aparece en una línea legible, por ejemplo:

```
[edge.rules] [temperature] NORMAL -> WARNING (motivo: lectura temperature=29.0)
[edge.lsc] Orden node-3 fan set {'duty': 50} [temperature] (motivo: lectura temperature=29.0)
```

Para que las ESP32 alcancen al edge en la maqueta, abre el puerto: `sudo ufw allow 5000/tcp`.

## Pruebas automáticas

```bash
python -m pytest -q
```

Hay unas 190 pruebas por dos seams: la app completa probada por HTTP con reloj, ESP32, MQTT y visión falsos, y `evaluate()` de cada módulo de reglas. No necesitan broker, cámara ni hardware. Una prueba se salta si no tienes los modelos ONNX; para correrla, mira [`docs/vision.md`](docs/vision.md).

## Probar el funcionamiento completo sin hardware

Con esto se recorre todo el flujo real: HTTP → reglas → órdenes a las ESP32 → SQLite → MQTT → broker. Las ESP32 se reemplazan por un simulador.

Abre **cuatro terminales** en la raíz del repo.

**Terminal 1: broker y edge**

```bash
docker compose up -d
python -m edge.app
```

**Terminal 2: las 3 ESP32 simuladas**

```bash
python tools/fake_esp32.py --heartbeat 5
```

El simulador levanta `node-1`, `node-2` y `node-3` en los puertos 8081 a 8083. Responde `POST /command` con `simulated: true` e imprime cada orden que recibe, por ejemplo `[node-3] fan set {'duty': 50} -> 50`. También manda heartbeats al edge para que conozca su IP y el estado de sus actuadores.

**Terminal 3: lo que llega al broker (la "nube")**

```bash
docker exec -it sentrya-mosquitto mosquitto_sub -t 'sentrya/#' -v
```

**Terminal 4: los escenarios**

Define atajos:

```bash
A=http://localhost:5000/api/v1
J='Content-Type: application/json'
temp() { curl -s -H "$J" -d "{\"node\":\"node-3\",\"deviceKey\":\"temperature\",\"metricType\":\"temperature\",\"value\":$1,\"unit\":\"C\"}" $A/readings; echo; }
hum()  { curl -s -H "$J" -d "{\"node\":\"node-2\",\"deviceKey\":\"humidity\",\"metricType\":\"humidity\",\"value\":$1,\"unit\":\"%\"}" $A/readings; echo; }
```

Mira el estado en cualquier momento con `curl -s $A/status`: subsistemas, actuadores (reportado y deseado), nodos, cola del outbox y conexión MQTT.

### 1. Temperatura (umbrales `demo`: T1 = 28 °C, T2 = 32 °C)

```bash
temp 26          # NORMAL
temp 29          # WARNING: ventilador al 50 %, muestreo cada 5 s
temp 33; temp 33; temp 33   # la 3.ª lectura ≥ T2 confirma CRITICAL
```

Qué debería pasar:

- **Simulador:** `fan 100`, `buzzer on` y `sampling 1`.
- **Broker:** cada lectura con su `localState`, y **un** mensaje `escalation` con `reason: temperature_critical`.

Para volver a NORMAL: `temp 30` (sigue en WARNING, porque la histéresis es de 1 °C) y luego `temp 26`.

### 2. Humedad (umbrales `demo`: encender < 40 %, apagar ≥ 55 %, alta > 65 %)

```bash
hum 35; hum 35; hum 35      # LOW: humidificador encendido
hum 56                      # humidificador apagado, NORMAL
curl -s -H "$J" -d '{"deviceKey":"humidifier","action":"mode","params":{"mode":"manual"}}' $A/demo/commands; echo
```

- En MANUAL las reglas ya no encienden el humidificador.
- La humedad se publica cada 60 s y en cada cambio de estado.
- Si dejas de enviar lecturas 30 s, aparece `device_error` (sensor caído).

### 3. Intrusión y validación del usuario

```bash
curl -s -H "$J" -d '{"known":false}' $A/simulate/security/person; echo   # persona desconocida
curl -s -H "$J" -d '{"node":"node-1"}' $A/security/sound; echo           # disparo del SA92, dentro de 10 s
```

Qué debería pasar:

- **Simulador:** `siren on` y `lock lock`.
- **Broker:** `intrusion` con `evidenceRef`.
- **Disco:** la foto de evidencia queda en `edge/data/evidence/`.
- **Estado:** queda en `INTRUSION` y se abre una ventana de 30 s.

Elige un final:

```bash
curl -s -H "$J" -d '{"decision":"confirm"}' $A/demo/security/validation; echo   # publica escalation (reason: confirmed)
curl -s -H "$J" -d '{"decision":"reject"}'  $A/demo/security/validation; echo   # apaga la sirena, vuelve a MONITORING
```

- Si no respondes en 30 s, se escala solo con `reason: timeout`.
- La sirena se apaga sola a los 15 s (perfil `demo`).
- **La cerradura nunca se desbloquea sola.** Para desbloquearla hace falta una orden explícita:

```bash
curl -s -H "$J" -d '{"deviceKey":"lock","action":"unlock"}' $A/demo/commands; echo
```

### 4. Órdenes manuales y válvula

```bash
curl -s -H "$J" -d '{"deviceKey":"valve","action":"open"}' $A/demo/commands; echo
curl -s -H "$J" -d '{"deviceKey":"fan","action":"set","params":{"duty":70}}' $A/demo/commands; echo
```

### 5. Broker caído: no se pierde nada

```bash
docker stop sentrya-mosquitto
temp 25; temp 25.5
curl -s $A/status          # "mqtt": {"connected": false}, "outbox": {"pending": 2}
docker start sentrya-mosquitto
# vuelve a abrir el mosquitto_sub de la terminal 3; en unos segundos llegan las 2 lecturas
curl -s $A/status          # "pending": 0
```

Mientras el broker está caído, los actuadores siguen respondiendo igual: la acción local nunca espera a la nube.

### 6. Nube → edge por MQTT

IoT Monitoring todavía no publica estos tópicos, pero el edge ya está suscrito. Puedes simularlos así:

```bash
docker exec sentrya-mosquitto mosquitto_pub -t 'sentrya/1/security/validation' -m '{"decision":"reject"}'
docker exec sentrya-mosquitto mosquitto_pub -t 'sentrya/1/REEMPLAZAR-ID-HUMEDAD/commands' -m '{"commandId":"c1","action":"mode","params":{"mode":"auto"}}'
docker exec sentrya-mosquitto mosquitto_pub -t 'sentrya/1/config' -m '{"temperature":{"t1_warning":27}}'
```

Los mensajes `config` con retain se ignoran a propósito: en cada arranque manda el YAML.

### 7. Reiniciar entre corridas de la demo

```bash
curl -s -X POST $A/demo/reset; echo
```

Todos los subsistemas vuelven al estado inicial y se apagan sirena, buzzer, ventilador y humidificador. El muestreo vuelve a 30 s. Se conserva el modo AUTO/MANUAL y la cerradura no se toca.

Si un sensor falla en vivo, `POST /api/v1/simulate/readings` acepta el mismo cuerpo que `/readings`, o varias lecturas con `{"readings": [...]}`.

## Ejecutar en la maqueta

1. **Broker:** en `.env`, pon `MQTT_BROKER_URL` apuntando al broker que use IoT Monitoring, o usa `docker compose up -d` en la laptop.
2. **Dispositivos:** reemplaza los `deviceId` de `edge/config/devices.yaml` por los ids reales. Los dispositivos tienen que estar registrados y activos en IoT Monitoring con los umbrales del perfil activo; si no, la nube rechaza las lecturas.
3. **ESP32:** configura en el firmware la IP de la laptop (`http://<ip-laptop>:5000/api/v1/...`). El edge aprende la IP de cada nodo por su heartbeat.
4. **Visión real (opcional):** sigue [`docs/vision.md`](docs/vision.md): modelos, rostros conocidos y los 11 pasos de verificación. Con `mock`, la persona se inyecta con `/simulate/security/person`.
5. **Calibración:** ajusta en `edge/config/profiles/demo.yaml` los umbrales de humedad a la humedad de la sala, y la confianza de visión a la luz.

## Problemas frecuentes

| Síntoma | Causa probable |
|---|---|
| `"mqtt": {"connected": false}` | El broker no está levantado o `MQTT_BROKER_URL` es incorrecto. Las lecturas esperan en el outbox. |
| Órdenes en `FAILED` | El nodo no mandó heartbeat (el edge no conoce su IP) o no responde en 2 s. Se reconcilian en el siguiente heartbeat. |
| `/simulate/*` responde 404 | `ENABLE_SIMULATION=false`. |
| Lecturas que no llegan a la nube | El `deviceId` de `devices.yaml` no existe o no está activo en IoT Monitoring. |
| `/status` muestra la seguridad `degraded` | Visión real sin cámara o sin modelos. El sonido y `/simulate/security/person` siguen funcionando. |
