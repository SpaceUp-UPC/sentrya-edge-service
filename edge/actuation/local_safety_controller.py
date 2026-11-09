"""Local Safety Controller: UNICO componente que envia ordenes a las ESP32.

Tracer bullet: solo recibe las acciones del Rules Engine. El envio real (cola, reintentos,
acuses, estado deseado) se implementa en el ticket de actuadores.
"""
from edge.shared.logging import get_logger

log = get_logger("lsc")


class LocalSafetyController:
    def __init__(self, db, esp32, clock):
        self.db = db
        self.esp32 = esp32
        self.clock = clock

    def execute(self, actions: list) -> None:
        for action in actions:
            log.info("Orden pendiente de implementar: %s %s %s",
                     action.actuator, action.action, action.params)
