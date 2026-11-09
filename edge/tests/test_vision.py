"""Vision real (#12) detras de las mismas interfaces que el mock.

Seam 1: la app armada por el factory con una CameraVision cuya camara, detector y
reconocedor son falsos; se observa por HTTP (/status, /simulate) y por la ESP32/MQTT falsos.
No requiere cv2: los cuadros son objetos opacos para todo lo que no es OpenCV.
"""
import pytest

from edge.security.camera import CameraVision, Detection

PNG = b"\x89PNG\r\n\x1a\n"


class FakeFrameSource:
    """Camara falsa: entrega los cuadros de `frames` en orden; None = camara caida."""

    def __init__(self, frames=None, *, down=False):
        self.frames = list(frames or [])
        self.down = down
        self.reads = 0
        self.last = None

    def read(self):
        if self.down:
            return None
        self.reads += 1
        if self.frames:
            self.last = self.frames.pop(0)
        return self.last if self.last is not None else f"frame-{self.reads}"


class FakePeopleDetector:
    """Detector de personas falso: `people[frame]` = detecciones en ese cuadro."""

    def __init__(self, people=None):
        self.people = people or {}
        self.seen = []

    def detect(self, frame):
        self.seen.append(frame)
        return self.people.get(frame, [])


class FakeFaceRecognizer:
    """Reconocedor falso: devuelve `matches[frame]` (una similitud o None) por persona."""

    def __init__(self, matches=None):
        self.matches = matches or {}

    def match(self, frame, boxes):
        return [self.matches.get(frame) for _ in boxes]


def encode(frame):
    return PNG + str(frame).encode()


def camera_vision(source=None, people=None, matches=None):
    return CameraVision(source or FakeFrameSource(), FakePeopleDetector(people),
                        FakeFaceRecognizer(matches), encode_png=encode)


def status(h):
    return h.client.get("/api/v1/status").get_json()


def test_without_camera_status_shows_security_degraded(make_harness):
    h = make_harness(_vision=camera_vision(FakeFrameSource(down=True)))
    edge = h.app.extensions["edge"]
    edge.camera_thread.step()
    security = status(h)["subsystems"]["security"]
    assert security["state"] == "MONITORING"
    assert security["degraded"] is True
    assert security["camera"] == "UNAVAILABLE"


def test_mock_vision_is_not_degraded(harness):
    security = status(harness)["subsystems"]["security"]
    assert security["degraded"] is False
    assert security["camera"] == "MOCK"


def sound(h):
    return h.client.post("/api/v1/security/sound",
                         json={"node": "node-1", "triggeredAt": "2026-01-01T12:00:00Z"})


def intrusions(h):
    return [m for m in h.mqtt.messages if m["payload"]["metricType"] == "intrusion"]


def actuations(h):
    return [(b["actuator"], b["action"]) for _, b in h.esp32.sent]


def evidence_bytes(h, msg):
    return (h.settings.evidence_dir / msg["payload"]["evidenceRef"].split("/", 1)[1]).read_bytes()


@pytest.fixture
def real(make_harness):
    """App con vision de camara: `real(frames, people, matches)` arma la app y su hilo de camara."""
    def build(frames=None, people=None, matches=None, down=False, **env):
        source = FakeFrameSource(frames, down=down)
        h = make_harness(_vision=camera_vision(source, people, matches), **env)
        h.heartbeat("node-1")
        h.camera = h.app.extensions["edge"].camera_thread
        h.source = source
        return h
    return build


def test_camera_thread_processes_frames_at_the_configured_fps(real):
    h = real(CAMERA_FPS="4")
    processed = 0
    for _ in range(16):          # 2 s de stream leido cada 0.125 s
        processed += h.camera.step()
        h.clock.advance(0.125)
    assert h.source.reads == 16  # se leen todos los cuadros (no se acumula retraso)...
    assert processed == 8        # ...pero solo se infieren 4 por segundo


@pytest.mark.parametrize("fps, expected", [("1", 2), ("30", 5)])
def test_camera_fps_is_bounded_to_2_to_5(real, fps, expected):
    h = real(CAMERA_FPS=fps)
    processed = 0
    for _ in range(40):          # 1 s leido cada 0.025 s
        processed += h.camera.step()
        h.clock.advance(0.025)
    assert processed == expected


def test_unknown_person_on_camera_plus_sound_is_intrusion_with_real_frame(real):
    h = real(frames=["cuadro-con-intruso"],
             people={"cuadro-con-intruso": [Detection(0.70, (10, 10, 50, 120))]},
             matches={"cuadro-con-intruso": 0.79})
    assert h.camera.step() is True
    sound(h)
    h.tick()
    assert actuations(h) == [("siren", "on"), ("lock", "lock")]
    (msg,) = intrusions(h)
    assert evidence_bytes(h, msg) == PNG + b"cuadro-con-intruso"


def test_known_face_on_camera_is_not_an_intruder(real):
    h = real(frames=["residente"], people={"residente": [Detection(0.95, (0, 0, 40, 90))]},
             matches={"residente": 0.80})
    h.camera.step()
    sound(h)
    h.tick()
    assert not intrusions(h) and not h.esp32.sent


def test_low_confidence_person_is_ignored(real):
    h = real(frames=["sombra"], people={"sombra": [Detection(0.69, (0, 0, 40, 90))]})
    h.camera.step()
    sound(h)
    h.tick()
    assert not intrusions(h)


def test_person_without_visible_face_is_unknown(real):
    h = real(frames=["espalda"], people={"espalda": [Detection(0.9, (0, 0, 40, 90))]})
    h.camera.step()
    sound(h)
    h.tick()
    assert len(intrusions(h)) == 1


def test_without_camera_sound_and_simulated_person_still_cause_intrusion(real):
    h = real(down=True)
    h.camera.step()
    assert h.client.post("/api/v1/simulate/security/person",
                         json={"known": False}).get_json()["event"] == "appeared"
    assert sound(h).get_json()["suspicious"] is True
    h.tick()
    assert actuations(h) == [("siren", "on"), ("lock", "lock")]
    (msg,) = intrusions(h)
    assert evidence_bytes(h, msg)[:8] == PNG  # sin cuadro: imagen de relleno
    assert status(h)["subsystems"]["security"]["degraded"] is True


def test_mock_is_the_default_vision():
    from edge.config.settings import Settings
    from edge.security.camera import build_vision
    from edge.security.person_detection import MockPersonDetector
    assert isinstance(build_vision(Settings.from_env({})), MockPersonDetector)
    assert isinstance(build_vision(Settings.from_env({"FACE_RECOGNIZER": "real"})), MockPersonDetector)


def test_real_vision_without_models_is_degraded_but_simulation_works(make_harness, tmp_path):
    from edge.config.settings import Settings
    from edge.security.camera import build_vision
    settings = Settings.from_env({"PERSON_DETECTOR": "real", "FACE_RECOGNIZER": "real",
                                  "CAMERA_STREAM_URL": "",
                                  "PERSON_MODEL_PATH": str(tmp_path / "no-existe.onnx"),
                                  "FACE_DETECTOR_MODEL_PATH": str(tmp_path / "no-existe.onnx"),
                                  "FACE_RECOGNIZER_MODEL_PATH": str(tmp_path / "no-existe.onnx")})
    h = make_harness(_vision=build_vision(settings))
    h.heartbeat("node-1")
    h.app.extensions["edge"].camera_thread.step()
    security = status(h)["subsystems"]["security"]
    assert security["degraded"] is True and security["camera"] == "UNAVAILABLE"
    assert security["problem"]
    h.client.post("/api/v1/simulate/security/person", json={"known": False})
    sound(h)
    h.tick()
    assert len(intrusions(h)) == 1


def test_camera_recovers_from_degraded(real):
    h = real(down=True)
    h.camera.step()
    assert status(h)["subsystems"]["security"]["degraded"] is True
    h.source.down = False
    h.camera.step()
    security = status(h)["subsystems"]["security"]
    assert (security["degraded"], security["camera"]) == (False, "OK")


class BlockingPeopleDetector:
    """Inferencia lenta: detect() se queda esperando hasta `release`."""

    def __init__(self):
        import threading
        self.entered, self.release = threading.Event(), threading.Event()

    def detect(self, frame):
        self.entered.set()
        self.release.wait(5)
        return []


def test_inference_runs_outside_the_edge_lock(make_harness):
    import threading
    people = BlockingPeopleDetector()
    h = make_harness(_vision=CameraVision(FakeFrameSource(), people, FakeFaceRecognizer(),
                                          encode_png=encode))
    edge = h.app.extensions["edge"]
    worker = threading.Thread(target=edge.camera_thread.step)
    worker.start()
    try:
        assert people.entered.wait(2)
        assert edge.lock.acquire(timeout=1)     # una lectura no espera a la inferencia
        edge.lock.release()
    finally:
        people.release.set()
        worker.join(5)
