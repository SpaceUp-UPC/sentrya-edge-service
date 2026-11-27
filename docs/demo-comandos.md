# Comandos para la demo

Guion para la exposición, pensado para **simular todo con comandos** y no depender de que la sala se caliente o se humedezca. Los comandos son para **Git Bash** en la laptop del edge.

## Antes de empezar

1. Arranca Mosquitto (Docker Desktop) y el edge.
2. Conecta los 3 nodos y espera a que el monitor serie de cada uno muestre `[heartbeat] ... HTTP 200`.
3. Abre **dos ventanas de Git Bash**.

**Ventana 1: lo que llega al broker** (déjala visible durante la demo):

```bash
docker exec sentrya-mosquitto mosquitto_sub -t 'sentrya/#' -v
```

**Ventana 2: los comandos.** Primero define la dirección del edge. Hazlo **cada vez que abras una ventana nueva**: si no, los comandos no hacen nada y no muestran error.

```bash
E=http://localhost:5000/api/v1
echo $E          # debe mostrar http://localhost:5000/api/v1
```

Para ver el estado de todo (nodos conectados, actuadores, conexión MQTT):

```bash
curl -s $E/status
```

Para dejar todo apagado y repetir la demo. Apaga sirena, buzzer, ventilador y humidificador; **no** desbloquea la cerradura:

```bash
curl -s -X POST $E/demo/reset
```

## Umbrales actuales (perfil `demo`)

Están en `edge/config/profiles/demo.yaml`. Si cambias algo, **reinicia el edge**.

| Parámetro | Valor | Qué significa |
|---|---|---|
| `temperature.t1_warning` | 32 °C | WARNING: ventilador al 50 % |
| `temperature.t2_critical` | 33 °C | 3 lecturas seguidas: CRITICAL (ventilador al 100 % + buzzer) |
| `temperature.hysteresis` | 0,5 °C | Cuánto debe bajar para volver de estado |
| `humidity.on_below` | 87 % | 3 lecturas seguidas debajo: humidificador encendido |
| `humidity.off_at` | 90 % | Desde aquí se apaga el humidificador |
| `humidity.high_above` | 95 % | 3 lecturas seguidas encima: HIGH |
| `security.correlation_window_seconds` | 10 s | Tiempo máximo entre persona y ruido para que sea intrusión |
| `security.validation_window_seconds` | 5 s | Tiempo para responder `confirm` / `reject` |
| `security.siren_max_seconds` | 5 s | Duración de la sirena tras confirmar o vencer la ventana |

> La ventana de validación de 5 s es corta para explicar en vivo. Si quieres tiempo para mostrar el `reject`, súbela a 15–30 s.

---

## Nodo 1: seguridad

### Intrusión (persona desconocida + ruido)

Manda los dos comandos seguidos (menos de 10 s entre uno y otro):

```bash
# Persona desconocida (simulada)
curl -s -X POST $E/simulate/security/person -H "Content-Type: application/json" -d '{"known":false}'

# Ruido fuerte (simulado; equivale a un disparo del sensor de sonido)
curl -s -X POST $E/security/sound -H "Content-Type: application/json" -d '{"node":"node-1"}'
```

En vez del segundo comando también puedes **aplaudir cerca del micrófono**.

**Qué pasa:** sirena encendida, cerradura bloqueada, `intrusion` en el broker y se abre la ventana de validación.

### Respuesta del usuario (dentro de la ventana de validación)

```bash
# El usuario confirma: se publica escalation y la sirena se apaga al cumplir su duración
curl -s -X POST $E/demo/security/validation -H "Content-Type: application/json" -d '{"decision":"confirm"}'

# El usuario dice que es una falsa alarma: sirena apagada al instante, sin escalamiento
curl -s -X POST $E/demo/security/validation -H "Content-Type: application/json" -d '{"decision":"reject"}'
```

Si nadie responde, al vencer la ventana se publica `escalation` con `reason: timeout`.

### Persona conocida (no dispara nada)

```bash
curl -s -X POST $E/simulate/security/person -H "Content-Type: application/json" -d '{"known":true}'
curl -s -X POST $E/security/sound -H "Content-Type: application/json" -d '{"node":"node-1"}'
```

Muestra que un ruido con una persona conocida **no** es intrusión.

### Cerradura

```bash
# Desbloquear: pulso de 3 s y se vuelve a bloquear sola
curl -s -X POST $E/demo/commands -H "Content-Type: application/json" -d '{"deviceKey":"lock","action":"unlock"}'

# Bloquear
curl -s -X POST $E/demo/commands -H "Content-Type: application/json" -d '{"deviceKey":"lock","action":"lock"}'
```

La cerradura **nunca** se desbloquea por una regla local, solo con una orden explícita como esta.

### Sirena a mano

```bash
curl -s -X POST $E/demo/commands -H "Content-Type: application/json" -d '{"deviceKey":"siren","action":"on"}'
curl -s -X POST $E/demo/commands -H "Content-Type: application/json" -d '{"deviceKey":"siren","action":"off"}'
```

### Cámara

Visor en el navegador: `http://<ip-del-nodo-1>:81/` (la IP sale en el heartbeat del monitor serie; con el hotspot ha sido `10.169.208.37`).

---

## Nodo 2: humedad

> **Ojo:** el DHT22 real sigue midiendo. Si la sala está por debajo de 87 %, en modo AUTO el humidificador **se enciende solo** tras 3 lecturas (~30 s). Para controlarlo solo con comandos, pásalo a MANUAL (abajo).

### Humedad baja: humidificador encendido

Las 3 lecturas van juntas para que no se meta una lectura real en medio:

```bash
curl -s -X POST $E/simulate/readings -H "Content-Type: application/json" -d '{"readings":[{"node":"node-2","deviceKey":"humidity","metricType":"humidity","value":80,"unit":"%"},{"node":"node-2","deviceKey":"humidity","metricType":"humidity","value":80,"unit":"%"},{"node":"node-2","deviceKey":"humidity","metricType":"humidity","value":80,"unit":"%"}]}'
```

**Qué pasa:** estado LOW, humidificador encendido, lectura en el broker.

### Humedad recuperada: humidificador apagado

```bash
curl -s -X POST $E/simulate/readings -H "Content-Type: application/json" -d '{"node":"node-2","deviceKey":"humidity","metricType":"humidity","value":91,"unit":"%"}'
```

> Si se apaga la luz del relé pero sigue saliendo niebla, mueve el cable verde o desenchufa el USB del humidificador (falta el transistor del relé, ver `docs/pendientes.md`).

### Humedad alta (HIGH)

```bash
curl -s -X POST $E/simulate/readings -H "Content-Type: application/json" -d '{"readings":[{"node":"node-2","deviceKey":"humidity","metricType":"humidity","value":97,"unit":"%"},{"node":"node-2","deviceKey":"humidity","metricType":"humidity","value":97,"unit":"%"},{"node":"node-2","deviceKey":"humidity","metricType":"humidity","value":97,"unit":"%"}]}'
```

**Qué pasa:** estado HIGH, humidificador apagado.

### Modo MANUAL / AUTO

```bash
# MANUAL: las reglas ya no lo encienden (sí lo apagan si el sensor cae o la humedad es alta)
curl -s -X POST $E/demo/commands -H "Content-Type: application/json" -d '{"deviceKey":"humidifier","action":"mode","params":{"mode":"manual"}}'

# Encender / apagar a mano
curl -s -X POST $E/demo/commands -H "Content-Type: application/json" -d '{"deviceKey":"humidifier","action":"on"}'
curl -s -X POST $E/demo/commands -H "Content-Type: application/json" -d '{"deviceKey":"humidifier","action":"off"}'

# Volver a AUTO
curl -s -X POST $E/demo/commands -H "Content-Type: application/json" -d '{"deviceKey":"humidifier","action":"mode","params":{"mode":"auto"}}'
```

### Sensor caído (opcional, con el hardware)

Desconecta el cable de datos del DHT22 del Nodo 2. A los ~30 s el humidificador se apaga y aparece `device_error` en el broker. Vuelve a conectarlo al terminar.

---

## Nodo 3: ambiental

> El DHT22 real manda una lectura cada pocos segundos y devuelve el estado a lo que mide la sala. Lo simulado dura poco: **mira el ventilador justo después del comando.**

### WARNING: ventilador al 50 %

```bash
curl -s -X POST $E/simulate/readings -H "Content-Type: application/json" -d '{"node":"node-3","deviceKey":"temperature","metricType":"temperature","value":32.5,"unit":"C"}'
```

### CRITICAL: ventilador al 100 % + buzzer + escalamiento

Las 3 lecturas van juntas:

```bash
curl -s -X POST $E/simulate/readings -H "Content-Type: application/json" -d '{"readings":[{"node":"node-3","deviceKey":"temperature","metricType":"temperature","value":34,"unit":"C"},{"node":"node-3","deviceKey":"temperature","metricType":"temperature","value":34,"unit":"C"},{"node":"node-3","deviceKey":"temperature","metricType":"temperature","value":34,"unit":"C"}]}'
```

**Qué pasa:** ventilador al 100 %, buzzer encendido (LED azul D2 de la ESP32, el buzzer es simulado) y `escalation` con `temperature_critical` en el broker. Con la siguiente lectura real baja de estado y se apaga.

### Ventilador y buzzer a mano

```bash
curl -s -X POST $E/demo/commands -H "Content-Type: application/json" -d '{"deviceKey":"fan","action":"set","params":{"duty":100}}'
curl -s -X POST $E/demo/commands -H "Content-Type: application/json" -d '{"deviceKey":"fan","action":"set","params":{"duty":0}}'

curl -s -X POST $E/demo/commands -H "Content-Type: application/json" -d '{"deviceKey":"buzzer","action":"on"}'
curl -s -X POST $E/demo/commands -H "Content-Type: application/json" -d '{"deviceKey":"buzzer","action":"off"}'
```

Si no hay ninguna regla activa, una lectura real puede apagarlos por su cuenta.

### Válvula (solo control remoto)

```bash
curl -s -X POST $E/demo/commands -H "Content-Type: application/json" -d '{"deviceKey":"valve","action":"open"}'
curl -s -X POST $E/demo/commands -H "Content-Type: application/json" -d '{"deviceKey":"valve","action":"close"}'
```

### Protección si se cae el edge (opcional)

Con el ventilador encendido, detén el edge (Ctrl+C). A los 60 s el Nodo 3 apaga ventilador y buzzer por su cuenta. Al volver el edge, reaplica el último estado pedido; usa `demo/reset` para dejarlo apagado.

---

## Si algo no responde

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| El `curl` no muestra nada | `E` no está definida en esa ventana | `E=http://localhost:5000/api/v1` |
| `404 Not Found` | Error al escribir la ruta (un punto o espacio de más) | Copia el comando de nuevo |
| La orden queda `FAILED` | El nodo no está conectado | Revisa su monitor serie; presiona EN/RST |
| `{"error": "no hay una ventana de validacion abierta..."}` | La ventana de validación ya venció | Repite la intrusión y responde más rápido |
| No aparece nada en el broker | Mosquitto apagado | Arranca el contenedor `sentrya-mosquitto` en Docker Desktop |
