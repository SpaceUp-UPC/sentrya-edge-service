# Pendientes

Dudas que no se resuelven solo con este repo. Mientras sigan abiertas, el código usa la propuesta de `AGENTS.md` detrás de una interfaz o configuración. Marca `[x]` cuando quede resuelta y anota la respuesta al lado.

## Equipo ESP32

### Contrato HTTP

- [ ] Confirmar los endpoints de `AGENTS.md` §8 (lecturas, sonido, heartbeat) y el servidor `POST /command` en cada nodo, con la respuesta `simulated` cuando falta el actuador.
- [ ] Nombres de los nodos: el edge acepta `node-1`, `node-2` y `node-3` (más los de `devices.yaml`). Confirmar los nombres reales.
- [ ] **¿El servidor HTTP de cada ESP32 atiende `POST /command` mientras su cliente HTTP espera la respuesta de una lectura, un disparo de sonido o un heartbeat?** Por si no, el edge nunca envía la orden dentro de esa petición: la registra y un hilo trabajador la envía en cuanto la petición se libera. Confirmar el comportamiento real del firmware.
- [ ] Intervalo de heartbeat. La reconciliación de actuadores depende de él (una orden fallida se recupera en el siguiente heartbeat).
- [ ] Formato de `actuators` en el heartbeat (propuesta del edge): `on`/`off` para siren, humidifier y buzzer; `locked`/`unlocked` para lock; `open`/`closed` para valve; duty numérico u `on`/`off` para fan.
- [ ] ¿Las ESP32 tienen hora (NTP)? Si no, el edge pone la marca de tiempo de llegada.

### Sensores

- [ ] Modelo del sensor de temperatura y su intervalo mínimo (hoy `sensor_min_interval_seconds: 1` en el perfil).
- [ ] Error del DHT22: el edge espera `POST /api/v1/readings` con `{ "node", "deviceKey": "humidity", "metricType": "humidity", "error": "dht22" }`, sin `value` ni `unit`. Confirmar.
- [ ] SA92: un `POST /api/v1/security/sound` por disparo, con el debounce hecho en la ESP32. El edge usa la hora de llegada, no `triggeredAt`.
- [ ] Stream de la ESP32-CAM en `http://<ip>:81/stream`.

### Actuadores

- [ ] Cerradura: confirmar la lógica real del relé. El edge manda `unlock` con `params.pulseSeconds` solo si `LOCK_ENERGIZE_TO_UNLOCK=true`; con `false` el edge no cambia nada y la polaridad queda en manos de la ESP32. Definir quién la aplica.
- [ ] Confirmar `node-2` / `deviceKey: humidity` y que el humidificador responde a `humidifier` on/off.

## Equipo IoT Monitoring

### Registro de dispositivos y `deviceId`

- [ ] **Cómo se obtienen los `deviceId` de `edge/config/devices.yaml`.** Las ESP32 no hablan con la nube; el edge publica en su nombre, pero IoT Monitoring rechaza lecturas de un `deviceId` que no tenga registrado y activo. Opciones:
  1. **Registro manual:** alguien da de alta los dispositivos en IoT Monitoring (app, API Gateway o base) y copia los ids generados en `devices.yaml`.
  2. **Ids acordados de antemano (recomendado para la demo):** el backend acepta ids fijos definidos juntos (por ejemplo `space1-temperature`) y los crea o activa una vez. Mismos valores en `devices.yaml` y en la nube, sin copiar nada.
  3. **Autoregistro del edge:** al arrancar, el edge llama a un endpoint de IoT Monitoring que crea los dispositivos y devuelve los ids. Requiere que ese endpoint exista y cambia la regla de `AGENTS.md` de que el edge no llama al backend por HTTP.
- [ ] Dispositivos a registrar, con los umbrales del perfil activo: `temperature`, `humidity`, `intrusion` y `escalation` (los dos últimos con umbral máximo 0).

### Contrato MQTT

- [ ] Agregar los `metricType` `humidity`, `intrusion`, `escalation` y `device_error` (hoy asume `temperature`, `voltage`, `sound`).
- [ ] `device_error` se publica con el `deviceId` del dispositivo `humidity` y `metricType: device_error`. Confirmar que lo aceptan así o si prefieren un dispositivo propio.
- [ ] Confirmar que umbral máximo 0 genera alerta con valor 1 ("lecturas evento", ADR 0001).
- [ ] Confirmar que ignoran los campos extra (`readingId`, `measuredAt`, `localState`, `reason`, `eventId`, `evidenceRef`).
- [ ] `evidenceRef` es una ruta local (`evidence/<eventId>.png`); la nube no puede descargar la foto. ¿Hace falta subirla o basta la referencia para la demo?
- [ ] Autenticación de dispositivos en el broker (hoy Mosquitto sin usuario ni contraseña, solo para desarrollo). ¿Qué broker se usa en la demo: el de IoT Monitoring o el `docker compose` de este repo?

### Nube → edge (todavía no lo implementan)

- [ ] Tópicos `commands`, `security/validation` y `config`. El edge ya está suscrito.
- [ ] `commands`: el segmento `{deviceId}` se resuelve con `devices.yaml`; si no coincide, se toma como nombre del actuador (`valve`, `lock`, `humidifier`). El `deviceId` de `humidity` se interpreta como el humidificador. Confirmar cómo identificarán los actuadores.
- [ ] `config`: el payload es el árbol de umbrales (como el YAML, parcial) o `{"thresholds": {...}}`. El edge **ignora los mensajes con flag retain**, porque el YAML manda en cada arranque (§6); solo aplica la config publicada mientras está conectado. Confirmar que les sirve.

## Calibración y verificación en la maqueta

- [ ] Umbrales de humedad del perfil `demo` según la humedad de la sala (`on_below`, `high_above`).
- [ ] Qué relés, MOSFET y fuentes estarán disponibles el día de la demo (lo que falte responde `simulated: true`).
- [ ] Visión real: seguir los 11 pasos de verificación de `docs/vision.md` y calibrar `person_confidence` y `face_match_confidence` con la luz de la sala. El puntaje de rostro escala el coseno de SFace para que 0.80 equivalga al corte 0.363 de OpenCV.
- [ ] Probar la prueba completa del `README.md` en la laptop con Ubuntu (hasta ahora solo se corrió en Windows).

## Decisiones del edge para validar con el equipo

Ya están implementadas; revisar que al equipo le sirvan así.

- [ ] **Duración de la sirena:** se cuenta desde que se encendió (al abrir la intrusión) y solo se apaga después de confirmar o vencer la ventana. Con el perfil `demo` (sirena 15 s, ventana 30 s), al vencer la ventana se escala y la sirena se apaga en el mismo momento. Alternativa: contar desde la confirmación o el vencimiento.
- [ ] **Reinicio con alarma activa:** la sirena se apaga directamente; no vuelve a sonar.
- [ ] **`/demo/reset`** también apaga el humidificador (además de sirena, buzzer y ventilador), devuelve el muestreo a 30 s, conserva el modo AUTO/MANUAL y no toca la cerradura. Responde las órdenes como `PENDING` porque el envío es asíncrono.
- [ ] **Detector de personas:** se usa YOLOX (ONNX) en lugar de MobileNet-SSD.

## Límites conocidos (deuda técnica)

No bloquean la demo; quedan para después.

- [ ] Las órdenes que llegan desde la nube se envían de forma síncrona dentro del tick: si un nodo no responde, el tick puede tardar hasta ~8 s (sin retener el lock).
- [ ] Una orden `PENDING` que se pierde en un reinicio pasa a `FAILED` y no se reenvía sola; la regla la vuelve a emitir en su siguiente transición, o la reconcilia el heartbeat.
- [ ] El cliente MQTT real (`PahoPublisher`) solo se probó contra Mosquitto local; la reconexión se probó con un cliente falso.
- [ ] Code smells del code review final que no se corrigieron (no afectan el comportamiento): `SecurityService` concentra varias responsabilidades; la secuencia accionar → sesión → encolar → flush se repite en varios lugares; agregar un actuador obliga a tocar varios mapas del catálogo; se usa `hasattr` para distinguir implementaciones.
- [ ] Revisar de nuevo con `/code-review` las correcciones del code review final (desde el commit `5083566`), si se quiere una segunda pasada.
