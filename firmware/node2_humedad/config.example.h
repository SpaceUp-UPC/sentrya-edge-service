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
#define NODE_ID "node-2"

// Pines
#define PIN_DHT   4    // DATA del DHT22
#define PIN_RELAY 25   // IN del rele del humidificador (D25 en la maqueta)
#define PIN_LED   2    // LED azul de la placa: refleja el estado del humidificador

// Sin rele: el LED simula el humidificador y se responde "simulated": true
#define HAS_HUMIDIFIER_RELAY true
#define RELAY_ACTIVE_LOW     true   // el modulo de 1 canal de la maqueta es activo en bajo

// Tiempos (ms)
#define READ_INTERVAL_MS      10000  // lectura del DHT22 (minimo 2000)
#define HEARTBEAT_INTERVAL_MS 10000  // heartbeat al edge
#define EDGE_LOST_OFF_MS      60000  // sin contacto con el edge: apaga el humidificador
