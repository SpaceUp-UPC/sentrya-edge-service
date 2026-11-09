# El sonido sospechoso se detecta por conteo de disparos, no por dB

El SA92 tiene salida digital: solo indica "ruido fuerte", no mide nivel ni permite clasificar. Por eso la regla es N disparos dentro de una ventana de M segundos (por defecto N = 1), con el debounce en la ESP32 y el conteo en el edge, en lugar del criterio de dB y clasificación del informe (5.6.3). Revertirlo requiere otro sensor, otro firmware y otro contrato en `/api/v1/security/sound`.
