# Desviaciones respecto del informe

| Desviación | Motivo | Detalle |
|---|---|---|
| El edge es una laptop con Ubuntu, no una Raspberry Pi | Hardware disponible | — |
| Sonido por conteo de disparos, sin dB ni clasificación | El SA92 es digital | [ADR 0002](adr/0002-sonido-por-conteo-de-disparos.md) |
| Sin A/C: CRITICAL solo activa ventilador al 100 % y buzzer | No hay A/C en la maqueta | — |
| La sirena es un buzzer pasivo | Hardware disponible | — |
| La válvula es solo remota; no hay sensor de fuga | Hardware disponible | — |
| La demo usa umbrales reducidos (perfil `demo`) | No se puede calentar la maqueta a 45–57 °C | `config/profiles/demo.yaml` |
| Orden de muestreo variable a la ESP32 (`sampling`) | Adaptar el muestreo al estado de temperatura | `AGENTS.md` §7 y §8 |
| Eventos publicados como "lecturas evento" | El backend aún no acepta otro tipo de eventos | [ADR 0001](adr/0001-eventos-como-lecturas-evento.md) |
