"""Implementaciones reales con OpenCV (solo `cv2` y numpy; nada de face_recognition ni PyTorch).

- `MjpegCameraSource`: stream MJPEG de la ESP32-CAM con `cv2.VideoCapture`.
- `YoloxPersonDetector`: YOLOX en ONNX del OpenCV Zoo con `cv2.dnn` (clase COCO 0 = persona).
- `SFaceRecognizer`: YuNet (rostros) + SFace (identidad) contra `data/known_faces/<nombre>/`.
- `encode_png`: el cuadro de evidencia.

`cv2` y numpy se importan al usarse: el resto del edge (y sus pruebas) no los necesita.
Los modelos se cargan una sola vez, al construir cada objeto. Dónde descargarlos: docs/vision.md.
"""
from pathlib import Path

from edge.security.camera import Detection
from edge.shared.logging import get_logger

log = get_logger("vision")

PERSON_CLASS_ID = 0                 # COCO: persona
YOLOX_INPUT = 640
YOLOX_STRIDES = (8, 16, 32)
CANDIDATE_CONFIDENCE = 0.30         # el umbral de la regla (0.70) lo aplica el servicio
NMS_THRESHOLD = 0.45
# OpenCV recomienda coseno >= 0.363 para "misma persona" con SFace. El coseno se escala para
# que ese corte valga 0.80, el umbral face_match_confidence de los perfiles: con el perfil por
# defecto se reconoce como lo recomienda OpenCV; subir el umbral exige mas parecido.
SFACE_COSINE_SAME_PERSON = 0.363
MATCH_SCORE_AT_CUTOFF = 0.80
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp"}


def _cv2():
    import cv2
    return cv2


def encode_png(frame) -> bytes:
    ok, buffer = _cv2().imencode(".png", frame)
    if not ok:
        raise ValueError("no se pudo codificar el cuadro")
    return buffer.tobytes()


class MjpegCameraSource:
    """`read()` devuelve el siguiente cuadro BGR o None si la camara no responde (reabre sola)."""

    def __init__(self, url: str):
        self.url = url
        self._capture = None

    def read(self):
        cv2 = _cv2()
        if not self.url:
            return None
        if self._capture is None:
            capture = cv2.VideoCapture(self.url)
            if not capture.isOpened():
                capture.release()
                return None
            capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            self._capture = capture
        ok, frame = self._capture.read()
        if not ok or frame is None:
            self._capture.release()
            self._capture = None
            return None
        return frame


# ---------------- detector de personas ----------------

def yolox_grids(input_size: int = YOLOX_INPUT, strides=YOLOX_STRIDES):
    """Rejillas y pasos por celda de la salida de YOLOX (como en el OpenCV Zoo)."""
    import numpy as np
    grids, steps = [], []
    for stride in strides:
        size = input_size // stride
        xv, yv = np.meshgrid(np.arange(size), np.arange(size))
        grid = np.stack((xv, yv), 2).reshape(-1, 2)
        grids.append(grid)
        steps.append(np.full((grid.shape[0], 1), stride))
    return np.concatenate(grids, 0), np.concatenate(steps, 0)


def decode_yolox_people(predictions, ratio: float, grids, steps,
                        min_confidence: float = CANDIDATE_CONFIDENCE, nms=None) -> list[Detection]:
    """Salida cruda de YOLOX (N x 85) -> personas en pixeles del cuadro original.

    `ratio` es la escala del letterbox (entrada / original). `nms(boxes, scores)` devuelve los
    indices a conservar (por defecto, cv2.dnn.NMSBoxes).
    """
    import numpy as np
    preds = np.asarray(predictions, dtype=np.float32).reshape(-1, 5 + 80)
    centers = (preds[:, :2] + grids) * steps
    sizes = np.exp(preds[:, 2:4]) * steps
    scores = preds[:, 4] * preds[:, 5 + PERSON_CLASS_ID]
    best_class = np.argmax(preds[:, 5:], axis=1)
    keep = (scores >= min_confidence) & (best_class == PERSON_CLASS_ID)
    if not keep.any():
        return []
    centers, sizes, scores = centers[keep] / ratio, sizes[keep] / ratio, scores[keep]
    boxes = [[float(cx - w / 2), float(cy - h / 2), float(w), float(h)]
             for (cx, cy), (w, h) in zip(centers, sizes)]
    if nms is None:
        nms = lambda b, s: _cv2().dnn.NMSBoxes(b, s, min_confidence, NMS_THRESHOLD)
    picked = [int(i) for i in np.array(nms(boxes, [float(s) for s in scores])).reshape(-1)]
    return [Detection(round(float(scores[i]), 4), tuple(int(round(v)) for v in boxes[i]))
            for i in sorted(picked, key=lambda i: -scores[i])]


class YoloxPersonDetector:
    """Personas en un cuadro BGR con YOLOX (ONNX) y `cv2.dnn`."""

    def __init__(self, model_path: Path):
        cv2 = _cv2()
        if not Path(model_path).is_file():
            raise FileNotFoundError(f"modelo de personas no encontrado: {model_path}")
        self.net = cv2.dnn.readNet(str(model_path))
        self.grids, self.steps = yolox_grids()
        log.info("Detector de personas YOLOX cargado (%s)", Path(model_path).name)

    def detect(self, frame) -> list[Detection]:
        import numpy as np
        cv2 = _cv2()
        height, width = frame.shape[:2]
        ratio = min(YOLOX_INPUT / height, YOLOX_INPUT / width)
        resized = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB),
                             (int(width * ratio), int(height * ratio)))
        padded = np.full((YOLOX_INPUT, YOLOX_INPUT, 3), 114.0, dtype=np.float32)
        padded[:resized.shape[0], :resized.shape[1]] = resized
        self.net.setInput(np.transpose(padded, (2, 0, 1))[np.newaxis])
        return decode_yolox_people(self.net.forward()[0], ratio, self.grids, self.steps)


# ---------------- reconocedor facial ----------------

def face_in_box(face_box, person_box) -> bool:
    """El centro del rostro (x, y, w, h) cae dentro de la caja de la persona."""
    fx, fy, fw, fh = face_box[:4]
    px, py, pw, ph = person_box
    cx, cy = fx + fw / 2, fy + fh / 2
    return px <= cx <= px + pw and py <= cy <= py + ph


def match_score(cosine: float) -> float:
    """Coseno de SFace -> puntaje 0..1 en el que 0.80 equivale al corte de OpenCV (0.363)."""
    return round(max(0.0, min(1.0, cosine * MATCH_SCORE_AT_CUTOFF / SFACE_COSINE_SAME_PERSON)), 4)


class SFaceRecognizer:
    """YuNet localiza rostros y SFace los compara con la galeria de rostros conocidos."""

    def __init__(self, detector_path: Path, recognizer_path: Path, known_faces_dir: Path):
        cv2 = _cv2()
        for path in (detector_path, recognizer_path):
            if not Path(path).is_file():
                raise FileNotFoundError(f"modelo de rostros no encontrado: {path}")
        self.detector = cv2.FaceDetectorYN.create(str(detector_path), "", (320, 320), 0.8, 0.3, 50)
        self.recognizer = cv2.FaceRecognizerSF.create(str(recognizer_path), "")
        self.known = self._load_gallery(Path(known_faces_dir))
        log.info("Reconocedor facial YuNet+SFace cargado: %d rostro(s) de %d persona(s)",
                 len(self.known), len({name for name, _ in self.known}))

    def _faces(self, image):
        height, width = image.shape[:2]
        self.detector.setInputSize((width, height))
        _, faces = self.detector.detect(image)
        return [] if faces is None else list(faces)

    def _feature(self, image, face):
        return self.recognizer.feature(self.recognizer.alignCrop(image, face))

    def _load_gallery(self, directory: Path) -> list:
        cv2 = _cv2()
        known = []
        if not directory.is_dir():
            log.warning("No existe %s: nadie cuenta como conocido", directory)
            return known
        for person_dir in sorted(p for p in directory.iterdir() if p.is_dir()):
            for photo in sorted(person_dir.iterdir()):
                if photo.suffix.lower() not in IMAGE_SUFFIXES:
                    continue
                image = cv2.imread(str(photo))
                faces = self._faces(image) if image is not None else []
                if not faces:
                    log.warning("Sin rostro en %s: se ignora", photo)
                    continue
                best = max(faces, key=lambda f: f[-1])
                known.append((person_dir.name, self._feature(image, best)))
        return known

    def match(self, frame, boxes) -> list[float | None]:
        """Por persona: puntaje del rostro conocido mas parecido, o None si no se ve el rostro."""
        cv2 = _cv2()
        faces = self._faces(frame)
        out = []
        for box in boxes:
            inside = [f for f in faces if face_in_box(f, box)]
            if not inside:
                out.append(None)
                continue
            feature = self._feature(frame, max(inside, key=lambda f: f[-1]))
            cosines = [self.recognizer.match(feature, known, cv2.FaceRecognizerSF_FR_COSINE)
                       for _, known in self.known]
            out.append(match_score(max(cosines, default=0.0)))
        return out
