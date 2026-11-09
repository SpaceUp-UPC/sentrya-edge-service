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
