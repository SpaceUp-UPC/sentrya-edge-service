"""Despachadores de ordenes del Local Safety Controller.

Las reglas deciden y registran la orden en la peticion que las disparo (lectura, sonido,
heartbeat), pero el envio HTTP a la ESP32 lo hace el despachador: asi la respuesta a la ESP32
que hizo la peticion no espera a que esa misma placa atienda la orden.

Interfaz: `submit(job) -> resultado | None`. `job` es una funcion sin argumentos que entrega
la orden y devuelve su `CommandResult`. Un despachador sincrono devuelve ese resultado; uno
asincrono devuelve None (la orden queda PENDING hasta que el trabajador la envia).
"""
import queue
import threading

from edge.shared.logging import get_logger

log = get_logger("dispatcher")


class SyncDispatcher:
    """Envia en el hilo que llama. Para pruebas deterministas (y scripts)."""

    def submit(self, job):
        return job()


class ThreadDispatcher:
    """Un hilo trabajador que envia las ordenes en orden de llegada, de inmediato."""

    def __init__(self):
        self._queue: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._loop, name="edge-commands", daemon=True)
        self._thread.start()

    def submit(self, job):
        self._queue.put(job)
        return None

    def stop(self) -> None:
        self._queue.put(None)

    def _loop(self) -> None:
        while True:
            job = self._queue.get()
            if job is None:
                return
            try:
                job()
            except Exception:  # una orden que falla nunca detiene al trabajador
                log.exception("Error enviando una orden")
