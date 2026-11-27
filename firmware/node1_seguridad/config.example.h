// Copia este archivo como config.h (en la misma carpeta) y completa tus datos.
// config.h esta en .gitignore: no subas tu clave Wi-Fi.
#pragma once

// Wi-Fi (la ESP32 solo usa redes de 2.4 GHz)
#define WIFI_SSID     "MI_RED"
#define WIFI_PASSWORD "MI_CLAVE"

// Laptop donde corre el edge (Flask en 0.0.0.0:5000). Es solo el respaldo: el nodo
// aprende la IP real del anuncio UDP del edge (DISCOVERY_PORT), aunque cambie.
#define EDGE_HOST "192.168.1.10"
#define EDGE_PORT 5000
#define DISCOVERY_PORT 5001

// Nombre del nodo tal como lo espera el edge
#define NODE_ID "node-1"

// Pines de la ESP32-CAM (la camara usa casi todos; no usar IO12: si esta en alto al
// encender, la placa no arranca)
#define PIN_SOUND      13   // OUT del sensor de sonido SA92 (digital)
#define PIN_SIREN      14   // I/O del modulo buzzer (pasivo, se le da un tono por PWM)
#define PIN_LOCK_RELAY 15   // IN del rele de la cerradura
#define PIN_FLASH      4    // LED flash de la placa: parpadea con la sirena

// Sensor de sonido
#define SOUND_ACTIVE_HIGH true   // OUT sube al detectar ruido (si dispara al reves: false)
#define SOUND_DEBOUNCE_MS 1000   // un disparo como maximo por segundo

// Sirena
#define BUZZER_IDLE_HIGH true    // modulo con transistor (I/O, VCC, GND): en reposo el pin en alto
#define SIREN_FLASH      true    // el flash acompana a la sirena
#define SIREN_MAX_MS     300000  // tope local; el edge la apaga antes (siren_max_seconds)

// Cerradura (rele en NO: con energia el pestillo se retrae = desbloqueada)
#define HAS_LOCK_RELAY   true
#define RELAY_ACTIVE_LOW false   // IO15 mueve un transistor que baja IN: pin en alto = rele activado
#define LOCK_PULSE_MS    3000    // si la orden unlock no trae pulseSeconds

// Camara: stream MJPEG en http://<ip>:81/stream (CAMERA_STREAM_URL del edge)
#define CAMERA_FRAME_SIZE FRAMESIZE_QVGA  // 320x240: fluido por el hotspot y suficiente para detectar personas
#define CAMERA_JPEG_QUALITY 12            // 10-63; menor = mejor calidad
#define CAMERA_FRAME_DELAY_MS 100         // pausa entre cuadros (~8 fps)
#define CAMERA_MAX_CLIENTS    3           // clientes del stream a la vez (edge + visores)

// Tiempos (ms)
#define HEARTBEAT_INTERVAL_MS 10000
#define EDGE_LOST_OFF_MS      60000  // sin contacto con el edge: apaga la sirena
