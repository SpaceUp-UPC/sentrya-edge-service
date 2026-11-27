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
#define NODE_ID "node-3"

// Pines (cableado de la maqueta)
#define PIN_DHT         4    // OUT del DHT22 (sensor de temperatura)
#define PIN_FAN         26   // resistencia -> transistor NPN del ventilador (PWM)
#define PIN_VALVE_RELAY 25   // IN1 del modulo de 2 reles (K1 = valvula 12 V)
#define PIN_LED         2    // LED azul de la placa: simula el buzzer

// Actuadores presentes. Lo que falta se simula y se responde "simulated": true.
#define HAS_FAN          true
#define HAS_VALVE_RELAY  true
#define HAS_BUZZER       false  // no hay buzzer en la maqueta: lo simula el LED
#define PIN_BUZZER       -1     // si se agrega uno, su pin aqui y HAS_BUZZER true
#define RELAY_ACTIVE_LOW true   // modulo con optoacoplador: activo en bajo

// Ventilador
#define FAN_ACTIVE_LOW  false  // NPN de la maqueta: pin en alto = gira (true solo para un PNP)
#define FAN_PWM_FREQ    1000   // Hz
#define FAN_KICK_MS     300    // arranque al 100 % antes de bajar a un duty parcial

// Tiempos (ms)
#define READ_INTERVAL_MS      5000   // muestreo inicial; el edge lo cambia con "sampling"
#define SENSOR_MIN_MS         2000   // el DHT22 necesita 2 s entre lecturas
#define HEARTBEAT_INTERVAL_MS 10000
#define EDGE_LOST_OFF_MS      60000  // sin contacto con el edge: apaga ventilador y buzzer
