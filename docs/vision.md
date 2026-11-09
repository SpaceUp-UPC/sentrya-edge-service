# Visión real (ESP32-CAM + OpenCV DNN)

La visión simulada es la de por defecto. Para usar la cámara y los modelos reales:

```
PERSON_DETECTOR=real
FACE_RECOGNIZER=real
CAMERA_STREAM_URL=http://<ip-esp32cam>:81/stream
```

Solo se usa OpenCV (`opencv-python-headless`, que trae `cv2.dnn`, `FaceDetectorYN` y
`FaceRecognizerSF`) y numpy. No se usan `face_recognition` (dlib), `ultralytics` ni PyTorch.

## Modelos (no se versionan)

Se descargan del [OpenCV Zoo](https://github.com/opencv/opencv_zoo) y se copian a
`edge/data/models/` (carpeta ignorada por git). Cada ruta se puede cambiar en `.env`.

| Uso | Archivo | URL | Variable |
|---|---|---|---|
| Personas (YOLOX‑S, ONNX, COCO) | `object_detection_yolox_2022nov.onnx` (~36 MB) | https://github.com/opencv/opencv_zoo/raw/main/models/object_detection_yolox/object_detection_yolox_2022nov.onnx | `PERSON_MODEL_PATH` |
| Rostros (YuNet) | `face_detection_yunet_2023mar.onnx` (~230 KB) | https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx | `FACE_DETECTOR_MODEL_PATH` |
| Identidad (SFace) | `face_recognition_sface_2021dec.onnx` (~37 MB) | https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx | `FACE_RECOGNIZER_MODEL_PATH` |

```
mkdir -p edge/data/models && cd edge/data/models
curl -LO https://github.com/opencv/opencv_zoo/raw/main/models/object_detection_yolox/object_detection_yolox_2022nov.onnx
curl -LO https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx
curl -LO https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx
```

Si un archivo pesa unos pocos bytes, es un puntero de Git LFS: descárgalo desde la página del
modelo en el Zoo. Los modelos se cargan una sola vez, al arrancar. Si falta uno (o `cv2`), el
edge no se cae: `/status` muestra la seguridad degradada con el motivo en `problem`, y el sonido
y `/simulate/security/person` siguen funcionando.

## Rostros conocidos

Una carpeta por persona en `edge/data/known_faces/<nombre>/` (`KNOWN_FACES_DIR`), con fotos
`.jpg`/`.png` de frente y bien iluminadas, idealmente tomadas con la misma ESP32-CAM. Se leen
al arrancar: para agregar a alguien, copia sus fotos y reinicia. Una foto sin rostro se ignora
(se avisa en la consola).

## Cómo decide

- El hilo de cámara lee el stream sin parar (para no acumular retraso) e infiere a `CAMERA_FPS`
  cuadros por segundo (por defecto 3; se acota a 2–5).
- Persona desconocida: YOLOX ve una persona con confianza ≥ `person_confidence` (0.70) y su
  rostro no coincide con ninguno conocido con puntaje ≥ `face_match_confidence` (0.80), o no se
  le ve el rostro. Un rostro "pertenece" a la persona si su centro cae dentro de la caja de ella.
- Puntaje de rostro: el coseno de SFace se escala para que el corte recomendado por OpenCV para
  "misma persona" (coseno 0.363) valga 0.80. Con el perfil por defecto se reconoce como lo
  recomienda OpenCV; subir `face_match_confidence` exige más parecido.
- La evidencia de una intrusión es el cuadro actual de la cámara, en PNG. Si no hay cámara
  (persona simulada), se guarda una imagen de relleno.

## Verificación manual contra la maqueta

No corre en CI. Con los modelos descargados y la ESP32-CAM en la red:

1. Prueba de modelos sin cámara: `EDGE_TEST_MODELS_DIR=edge/data/models pytest edge/tests/test_opencv_vision.py`.
2. Abre `http://<ip-esp32cam>:81/stream` en el navegador de la laptop: debe verse el video.
3. Registra un rostro: 2–3 fotos tuyas en `edge/data/known_faces/<tu-nombre>/`.
4. Arranca el edge con `PERSON_DETECTOR=real`, `FACE_RECOGNIZER=real` y `CAMERA_STREAM_URL`.
   En la consola deben aparecer "Detector de personas YOLOX cargado", "Reconocedor facial
   YuNet+SFace cargado: N rostro(s)", "Hilo de camara iniciado" y "Camara disponible".
5. `GET /api/v1/status`: `subsystems.security` con `camera: "OK"` y `degraded: false`.
6. Ponte frente a la cámara: no debe aparecer "Persona desconocida detectada".
7. Que otra persona (no registrada) se ponga frente a la cámara: aparece "Persona desconocida
   detectada". Dentro de 10 s, dispara el SA92 (o `POST /api/v1/security/sound`): sirena,
   cerradura e intrusión. Revisa que `edge/data/evidence/<eventId>.png` sea un cuadro de la cámara.
8. De espaldas (sin rostro visible) también debe contar como desconocida.
9. Desconecta la ESP32-CAM: en unos segundos la consola dice "Camara NO disponible" y `/status`
   muestra `camera: "UNAVAILABLE"`, `degraded: true`. `POST /api/v1/simulate/security/person`
   con `{"known": false}` más un disparo de sonido siguen provocando la intrusión.
10. Reconecta la cámara: vuelve `camera: "OK"` sin reiniciar el edge.
11. Si el reconocimiento falla con la luz de la sala, ajusta `face_match_confidence` y
    `person_confidence` en `edge/config/profiles/demo.yaml` y reinicia.
