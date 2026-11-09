"""Person Detection Service y su interfaz de deteccion.

`PersonDetector` es la interfaz (la implementacion real con OpenCV DNN llega aparte):
    detect() -> list[PersonObservation]   personas vistas en el ultimo cuadro
    capture_evidence() -> bytes           foto (PNG/JPEG) del momento
    available: bool                       False si la camara no responde (degradado)
El detector mock es el de por defecto y recibe las personas de /simulate/security/person.
"""
import struct
import zlib
from dataclasses import dataclass

from edge.shared.logging import get_logger

log = get_logger("person")


@dataclass(frozen=True)
class PersonObservation:
    confidence: float                 # confianza de "es una persona"
    face_match: float | None = None   # similitud con el rostro conocido mas parecido; None = rostro no visible


def _png(width: int, height: int, rgb) -> bytes:
    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))
    row = b"\x00" + bytes(rgb) * width
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(row * height))
            + chunk(b"IEND", b""))


class MockPersonDetector:
    """Detector simulado: cada `inject` es una persona visible en el proximo cuadro."""

    available = True

    def __init__(self):
        self._pending: list[PersonObservation] = []

    def inject(self, known: bool) -> None:
        self._pending.append(PersonObservation(0.95, 0.95 if known else None))

    def detect(self) -> list[PersonObservation]:
        seen, self._pending = self._pending, []
        return seen

    def capture_evidence(self) -> bytes:
        return _png(160, 120, (200, 30, 30))


class PersonDetectionService:
    """Interpreta cuadros: guarda la marca de la ultima persona desconocida.

    Solo emite evento al aparecer (de ausente a presente) y respeta el cooldown.
    """

    def __init__(self, detector, clock, thresholds: dict):
        self.detector = detector
        self.clock = clock
        self._thresholds = thresholds  # se leen al usarse: el perfil puede cambiar
        self.last_unknown_person_at: float | None = None
        self._unknown_present = False

    def is_unknown(self, obs: PersonObservation) -> bool:
        sec = self._thresholds["security"]
        if obs.confidence < sec["person_confidence"]:
            return False
        return obs.face_match is None or obs.face_match < sec["face_match_confidence"]

    def process_frame(self) -> str | None:
        """Procesa un cuadro. Devuelve 'appeared', 'cooldown' o None (sin cambio de interes)."""
        unknown = any(self.is_unknown(o) for o in self.detector.detect())
        if not unknown:
            self._unknown_present = False
            return None
        if self._unknown_present:
            return None
        self._unknown_present = True
        now = self.clock.monotonic()
        if self.last_unknown_person_at is not None and now - self.last_unknown_person_at < self._thresholds["security"]["unknown_person_cooldown_seconds"]:
            log.info("Persona desconocida en cooldown: se ignora")
            return "cooldown"
        self.last_unknown_person_at = now
        log.info("Persona desconocida detectada")
        return "appeared"
