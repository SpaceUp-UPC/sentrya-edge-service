# Pendientes con otros equipos

Dudas que no se resuelven en este repo. Mientras sigan abiertas, el código usa la propuesta de `AGENTS.md` detrás de una interfaz o configuración.

## Equipo ESP32

- [ ] Confirmar los endpoints de `AGENTS.md` §8 (lecturas, sonido, heartbeat).
- [ ] Servidor `POST /command` en cada nodo, con la respuesta `simulated` cuando falta el actuador.
- [ ] Debounce del SA92.
- [ ] Stream de la ESP32-CAM en el puerto 81.
- [ ] Lógica real de la cerradura (`LOCK_ENERGIZE_TO_UNLOCK`, `LOCK_PULSE_SECONDS`).
- [ ] Modelo del sensor de temperatura y su intervalo mínimo.
- [ ] ¿Las ESP32 tienen hora (NTP)? Si no, el edge pone la marca de tiempo.
- [ ] Intervalo de heartbeat (la reconciliación de actuadores depende de él).
- [ ] Cerradura: el edge manda `unlock` con `params.pulseSeconds` solo si `LOCK_ENERGIZE_TO_UNLOCK=true`; confirmar quién aplica la polaridad en la ESP32.
- [ ] ¿El servidor HTTP de cada ESP32 (`POST /command`) atiende mientras su cliente HTTP espera la respuesta de una lectura, un disparo de sonido o un heartbeat? Por si no, el edge nunca envía la orden dentro de esa petición: la registra y un hilo trabajador la envía en cuanto la petición se libera (despachador del Local Safety Controller). Confirmar el comportamiento real.
- [ ] Heartbeat: el edge acepta `node-1`, `node-2`, `node-3` (más los de `devices.yaml`); confirmar los nombres reales.

## Equipo IoT Monitoring

- [ ] Agregar los `metricType` `humidity`, `intrusion`, `escalation` y `device_error`.
- [ ] Registrar los dispositivos con los umbrales del perfil activo.
- [ ] Confirmar que umbral máximo 0 genera alerta con valor 1.
- [ ] Confirmar que se ignoran los campos extra (`readingId`, `measuredAt`, `localState`, `reason`, `evidenceRef`).
- [ ] Tópicos nube → edge: `commands`, `security/validation`, `config`.
- [ ] Autenticación de dispositivos en el broker.

## Calibración en la maqueta

- [ ] Umbrales de humedad del perfil `demo` según la humedad de la sala.
- [ ] Qué relés, MOSFET y fuentes estarán disponibles el día de la demo.
- [ ] Visión real (ticket #12): calibrar `person_confidence` y `face_match_confidence` con la luz de la sala y la ESP32-CAM (el puntaje de rostro escala el coseno de SFace para que 0.80 sea el corte 0.363 de OpenCV; ver `docs/vision.md`). Seguir la verificación manual de `docs/vision.md`.

## Humedad (#7)

- [ ] Equipo ESP32: error del DHT22 se reporta como `POST /api/v1/readings` con `{ "node", "deviceKey": "humidity", "metricType": "humidity", "error": "dht22" }` (sin `value` ni `unit`). Propuesta del edge; confirmar.
- [ ] Equipo ESP32: confirmar `node-2` / `deviceKey: humidity` y que el humidificador responde a `humidifier` on/off.
## Seguridad (ticket #8)

- [ ] Equipo ESP32: confirmar que el SA92 llega como un POST por disparo a `/api/v1/security/sound` (el edge usa la hora de llegada, no `triggeredAt`).
- [ ] Equipo IoT Monitoring: registrar el dispositivo `intrusion` (`REEMPLAZAR-ID-INTRUSION` en `devices.yaml`); `evidenceRef` es una ruta relativa local (`evidence/<eventId>.png`), la nube no puede descargarla todavia.

- **Formato de `actuators` en el heartbeat (ticket #6):** el edge compara el deseado con los valores `on`/`off` (siren, humidifier, buzzer), `locked`/`unlocked` (lock), `open`/`closed` (valve) y duty numerico u `on`/`off` (fan). Confirmar con quien programa las ESP32. Un `unlock` aplicado nunca se reconcilia (la cerradura se vuelve a cerrar sola tras el pulso).

## Nube -> edge (ticket #11)

- [ ] Tópico `commands`: el segmento `{deviceId}` se resuelve con `devices.yaml`; si no coincide, se toma como nombre lógico del actuador (`valve`, `lock`, `humidifier`). El `deviceId` del dispositivo `humidity` se interpreta como el humidificador. Confirmar con IoT Monitoring cómo identificará los actuadores.
- [ ] `config` (retained): el payload es el árbol de umbrales (igual que el YAML, parcial) o `{"thresholds": {...}}`. Como es retained, el broker lo reenvía tras cada reconexión o reinicio, y volverá a pisar el YAML; confirmar si la nube lo limpia.
