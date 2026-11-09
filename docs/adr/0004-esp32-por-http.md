# Las ESP32 hablan HTTP con el edge, no MQTT

Las ESP32 envían lecturas y eventos al edge por HTTP/JSON en la red local y reciben órdenes en `POST /command`; solo el edge habla MQTT con la nube. Así el edge es el único punto que valida, aplica las reglas y acciona localmente sin depender del broker, y el firmware queda más simple. La alternativa (ESP32 publicando directo en Mosquitto) se descartó porque la decisión local dependería del broker y el contrato con el equipo de firmware cambiaría por completo.
