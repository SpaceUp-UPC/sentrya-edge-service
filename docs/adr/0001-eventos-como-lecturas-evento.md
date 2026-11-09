# Los eventos se publican como "lecturas evento"

IoT Monitoring (Go) solo acepta lecturas en `sentrya/+/+/readings`, así que la intrusión, el escalamiento y el sensor caído se publican como lecturas con `value: 1` y `unit: "event"`, sobre dispositivos registrados en la nube con umbral máximo 0 para que generen alerta. Se eligió esto en lugar de esperar un canal de eventos propio en el backend, que no existe todavía. Cambiarlo obliga a modificar a la vez el edge, el outbox y el servicio en Go.
