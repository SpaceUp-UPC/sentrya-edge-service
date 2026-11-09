"""Vision real: camara ESP32-CAM, hilo de inferencia y la vision que ve el Person Detection Service.

`CameraVision` cumple la misma interfaz que `MockPersonDetector` (detect, capture_evidence,
available, inject) y se arma con tres piezas intercambiables:
    source.read() -> frame | None                   camara (None = no disponible)
    people.detect(frame) -> list[Detection]         detector de personas (OpenCV DNN)
    faces.match(frame, boxes) -> list[float|None]   reconocedor facial (YuNet + SFace)
Los cuadros son opacos para este modulo: solo OpenCV los interpreta (edge/security/opencv_vision.py).

`CameraThread` es el hilo propio que lee la camara y procesa 2-5 cuadros por segundo.
Si la camara no responde, el subsistema de seguridad sigue vivo y /status lo marca degradado.
"""
import threading
from dataclasses import dataclass

from edge.security.person_detection import MockPersonDetector, PersonObservation
from edge.shared.logging import get_logger

log = get_logger("camera")

MIN_FPS, MAX_FPS = 2.0, 5.0


@dataclass(frozen=True)
class Detection:
    confidence: float                 # confianza de "es una persona"
    box: tuple[int, int, int, int]    # x, y, ancho, alto en pixeles del cuadro


class MockFaceRecognizer:
    """Reconocedor simulado (FACE_RECOGNIZER=mock con camara real).

    Por defecto toda persona de la camara es desconocida. `POST /simulate/security/faces`
    con `{"known": true}` hace que las personas que ve la camara se reconozcan como conocidas
    (p. ej. mientras pasa un residente en la demo) hasta volver a `{"known": false}`.
    """

    MATCH = 0.95   # similitud simulada de un rostro conocido (sobre cualquier corte razonable)

    def __init__(self):
        self.known = False

    def set_known(self, known: bool) -> None:
        self.known = bool(known)
        log.info("Reconocedor simulado: las personas de la camara son %s",
                 "conocidas" if self.known else "desconocidas")

    def match(self, frame, boxes):
        return [self.MATCH if self.known else None for _ in boxes]


class CameraVision:
    """Vision con camara real. Las personas inyectadas por /simulate se suman al cuadro actual."""

    def __init__(self, source, people, faces, *, encode_png=None, problem: str | None = None):
        self.source = source
        self.people = people
        self.faces = faces
        self.problem = problem            # p. ej. modelos que no cargaron: degradado
        self._encode = encode_png
        self._frame = None
        self._camera_ok = False
        self._ever_polled = False
        self._pending: list[PersonObservation] = []
        self._lock = threading.Lock()

    # ---- camara ----

    @property
    def available(self) -> bool:
        return self._camera_ok and self.problem is None

    @property
    def camera_status(self) -> str:
        return "OK" if self._camera_ok else "UNAVAILABLE"

    def poll(self) -> bool:
        """Lee un cuadro de la camara. Devuelve si hay cuadro nuevo."""
        try:
            frame = self.source.read()
        except Exception:
            log.exception("Error leyendo la camara")
            frame = None
        ok = frame is not None
        if ok != self._camera_ok or not self._ever_polled:
            log.info("Camara %s", "disponible" if ok else "NO disponible: seguridad degradada")
        self._ever_polled = True
        with self._lock:
            self._frame = frame
            self._camera_ok = ok
        return ok

    # ---- interfaz del detector (igual que el mock) ----

    def inject(self, known: bool) -> None:
        with self._lock:
            self._pending.append(PersonObservation(0.95, 0.95 if known else None))

    def detect(self) -> list[PersonObservation]:
        with self._lock:
            seen, self._pending = self._pending, []
            frame = self._frame
        if frame is None or self.problem is not None:
            return seen
        detections = self.people.detect(frame)
        if detections:
            matches = self.faces.match(frame, [d.box for d in detections])
            seen += [PersonObservation(d.confidence, m) for d, m in zip(detections, matches)]
        return seen

    def capture_evidence(self) -> bytes:
        """El cuadro actual de la camara; sin camara (persona simulada), una imagen de relleno."""
        with self._lock:
            frame = self._frame
        if frame is None:
            log.info("Sin cuadro de camara: la evidencia es una imagen de relleno")
            return MockPersonDetector().capture_evidence()
        if self._encode is None:
            from edge.security.opencv_vision import encode_png
            self._encode = encode_png
        return self._encode(frame)


class _NoPeople:
    """Detector de reemplazo cuando el modelo real no cargo (la vision queda degradada)."""

    def detect(self, frame):
        return []


def build_vision(settings):
    """Vision segun PERSON_DETECTOR / FACE_RECOGNIZER (mock por defecto). Carga los modelos una vez.

    Si un modelo o cv2 falta, no se cae: la vision queda degradada y la simulacion sigue.
    """
    if settings.person_detector != "real":
        if settings.face_recognizer == "real":
            log.warning("FACE_RECOGNIZER=real requiere PERSON_DETECTOR=real: se usa el mock")
        return MockPersonDetector()
    problems = []
    try:
        from edge.security.opencv_vision import MjpegCameraSource, YoloxPersonDetector
        source = MjpegCameraSource(settings.camera_stream_url)
    except Exception as error:  # cv2 no instalado
        log.error("Vision real no disponible: %s", error)
        return CameraVision(_NoCamera(), _NoPeople(), MockFaceRecognizer(), problem=str(error))
    try:
        people = YoloxPersonDetector(settings.person_model_path)
    except Exception as error:
        log.error("No se pudo cargar el detector de personas: %s", error)
        people, problems = _NoPeople(), problems + [str(error)]
    faces = MockFaceRecognizer()
    if settings.face_recognizer == "real":
        try:
            from edge.security.opencv_vision import SFaceRecognizer
            faces = SFaceRecognizer(settings.face_detector_model_path,
                                    settings.face_recognizer_model_path, settings.known_faces_dir)
        except Exception as error:
            log.error("No se pudo cargar el reconocedor facial: %s", error)
            problems.append(str(error))
    if not settings.camera_stream_url:
        problems.append("CAMERA_STREAM_URL vacio")
    return CameraVision(source, people, faces, problem="; ".join(problems) or None)


class _NoCamera:
    def read(self):
        return None


def vision_status(vision) -> dict:
    """Campos de camara para /status (subsystems.security)."""
    if not hasattr(vision, "camera_status"):
        return {"camera": "MOCK", "degraded": not getattr(vision, "available", True)}
    out = {"camera": vision.camera_status, "degraded": not vision.available}
    if vision.problem:
        out["problem"] = vision.problem
    return out


class CameraThread:
    """Lee la camara y entrega cuadros al servicio de seguridad a `fps` (acotado a 2-5)."""

    def __init__(self, vision, security, clock, fps: float = 3, retry_seconds: float = 2.0):
        self.vision = vision
        self.security = security
        self.clock = clock
        self.fps = min(max(float(fps), MIN_FPS), MAX_FPS)
        self.interval = 1.0 / self.fps
        self.retry_seconds = retry_seconds
        self._next_at: float | None = None
        self._thread = None

    def step(self) -> bool:
        """Lee un cuadro y lo procesa si toca segun los fps. Devuelve si se proceso."""
        if not self.vision.poll():
            return False
        now = self.clock.monotonic()
        if self._next_at is not None and now < self._next_at:
            return False  # se leyo para vaciar el buffer del stream, pero no se infiere
        if self._next_at is None or now - self._next_at >= self.interval:
            self._next_at = now + self.interval
        else:
            self._next_at += self.interval
        self.security.process_frame()
        return True

    def start(self, stop: threading.Event) -> None:
        """Arranca el hilo (una sola vez, desde el factory)."""
        def loop():
            log.info("Hilo de camara iniciado a %.1f fps", self.fps)
            while not stop.is_set():
                try:
                    self.step()
                    if not self.vision.available and not stop.is_set():
                        stop.wait(self.retry_seconds)  # camara caida: reintenta sin saturar
                except Exception:
                    log.exception("Error en el hilo de camara")
                    stop.wait(self.retry_seconds)
        self._thread = threading.Thread(target=loop, name="edge-camera", daemon=True)
        self._thread.start()
