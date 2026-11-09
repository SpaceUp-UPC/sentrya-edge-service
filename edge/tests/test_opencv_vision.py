"""Piezas OpenCV de la vision real (#12).

Las que necesitan cv2/numpy se saltan si no estan instalados. Las que cargan los modelos reales
corren solo si EDGE_TEST_MODELS_DIR apunta a la carpeta con los .onnx (docs/vision.md).
"""
import math
import os
from pathlib import Path

import pytest

from edge.security.camera import Detection
from edge.security.opencv_vision import face_in_box, match_score


def test_face_belongs_to_the_person_whose_box_contains_its_center():
    person = (100, 50, 80, 200)
    assert face_in_box((120, 60, 40, 40, 0.9), person)
    assert not face_in_box((300, 60, 40, 40, 0.9), person)
    assert not face_in_box((170, 60, 40, 40, 0.9), person)  # centro en x=190, fuera


def test_opencv_same_person_cutoff_maps_to_the_profile_threshold():
    assert match_score(0.363) == 0.80          # corte recomendado por OpenCV para SFace
    assert match_score(0.30) < 0.80
    assert match_score(0.9) == 1.0 and match_score(-0.2) == 0.0


def test_png_evidence_is_the_frame_itself():
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    from edge.security.opencv_vision import encode_png
    frame = np.zeros((48, 64, 3), dtype=np.uint8)
    frame[10:20, 5:30] = (0, 0, 255)
    data = encode_png(frame)
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert (cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR) == frame).all()


def test_yolox_output_is_decoded_into_people_in_original_pixels():
    np = pytest.importorskip("numpy")
    from edge.security.opencv_vision import decode_yolox_people, yolox_grids
    grids, steps = yolox_grids()
    preds = np.zeros((8400, 85), dtype=np.float32)
    person = 20 * 80 + 10                        # celda (x=10, y=20) de la rejilla de paso 8
    preds[person, :5] = [0.5, 0.5, math.log(4), math.log(8), 0.9]
    preds[person, 5] = 0.9                       # clase 0 = persona
    car = 30 * 80 + 40
    preds[car, :5] = [0, 0, 0, 0, 0.95]
    preds[car, 5 + 2] = 0.95                     # clase 2 = auto: se ignora
    faint = 5
    preds[faint, 4], preds[faint, 5] = 0.4, 0.4  # 0.16: por debajo del minimo
    keep_all = lambda boxes, scores: list(range(len(boxes)))
    # centro (10.5, 20.5) * 8 = (84, 164); tamano (32, 64); letterbox 0.5 -> x2 en el original
    assert decode_yolox_people(preds, 0.5, grids, steps, nms=keep_all) == [
        Detection(0.81, (136, 264, 64, 128))]


MODELS = os.environ.get("EDGE_TEST_MODELS_DIR")
needs_models = pytest.mark.skipif(not MODELS, reason="EDGE_TEST_MODELS_DIR no definido")


@needs_models
def test_real_models_load_once_and_see_nobody_in_an_empty_frame(tmp_path):
    pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    from edge.security.opencv_vision import SFaceRecognizer, YoloxPersonDetector
    models = Path(MODELS)
    people = YoloxPersonDetector(models / "object_detection_yolox_2022nov.onnx")
    faces = SFaceRecognizer(models / "face_detection_yunet_2023mar.onnx",
                            models / "face_recognition_sface_2021dec.onnx", tmp_path / "known_faces")
    frame = np.full((240, 320, 3), 127, dtype=np.uint8)
    assert people.detect(frame) == []
    assert faces.match(frame, [(0, 0, 320, 240)]) == [None]
