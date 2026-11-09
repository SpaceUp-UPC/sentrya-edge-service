"""Sound Analysis Service: N disparos del SA92 dentro de M segundos = sonido sospechoso (ADR 0002).

La ESP32 aplica el debounce; el edge cuenta. Se usa la hora de llegada (reloj monotonico),
no `triggeredAt`, porque la ESP32 puede no tener hora.
"""
from collections import deque

from edge.shared.logging import get_logger

log = get_logger("sound")


class SoundAnalysisService:
    def __init__(self, clock, triggers_required: int = 1, window_seconds: float = 3):
        self.clock = clock
        self.triggers_required = max(1, int(triggers_required))
        self.window_seconds = window_seconds
        self.last_suspicious_sound_at: float | None = None
        self._shots: deque = deque()

    def record_shot(self) -> bool:
        """Registra un disparo. Devuelve True si ahora el sonido es sospechoso."""
        now = self.clock.monotonic()
        self._shots.append(now)
        while self._shots and now - self._shots[0] > self.window_seconds:
            self._shots.popleft()
        if len(self._shots) < self.triggers_required:
            log.info("Disparo de sonido %d/%d en %ss: aun no es sospechoso",
                     len(self._shots), self.triggers_required, self.window_seconds)
            return False
        self.last_suspicious_sound_at = now
        log.info("Sonido sospechoso: %d disparos en %ss", len(self._shots), self.window_seconds)
        return True
