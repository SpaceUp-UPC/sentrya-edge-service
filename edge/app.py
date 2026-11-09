"""Arranque: factory de la app con adaptadores inyectables y entrada tick periodica."""
import os
import threading

from flask import Flask

from edge.actuation.local_safety_controller import LocalSafetyController
from edge.communication.cloud_inbox import CloudInbox
from edge.communication.events import enqueue_events
from edge.communication.iot_client import IoTCommunicationClient
from edge.config import thresholds as thresholds_config
from edge.config.devices import load_devices
from edge.config.settings import Settings
from edge.rules_engine import Action, RulesEngine
from edge.sensors import api
from edge.sensors.sensor_manager import SensorManager
from edge.security import rules as security_rules
from edge.security.camera import CameraThread, build_vision
from edge.security.service import SecurityService
from edge.shared.clock import SystemClock
from edge.shared.db import Database
from edge.shared.logging import configure_logging, get_logger
from edge.shared.retention import purge
from edge.humidity import rules as humidity_rules
from edge.temperature import rules as temperature_rules

log = get_logger("app")


def default_modules() -> list:
    """Registro de modulos de reglas. Un subsistema nuevo se agrega aqui."""
    return [temperature_rules.MODULE, humidity_rules.MODULE, security_rules.MODULE]


class Edge:
    """Contenedor de componentes; vive en app.extensions['edge']."""

    def __init__(self, settings, clock, db, esp32, publisher, vision, modules, dispatcher=None):
        self.settings = settings
        self.clock = clock
        self.db = db
        self.esp32 = esp32
        self.publisher = publisher
        self.vision = vision
        self.profile = settings.threshold_profile
        purge(db, clock, settings.retention_days)
        thresholds = thresholds_config.apply_profile(db, settings.profiles_dir, self.profile, clock)
        self.devices = load_devices(settings.devices_path)
        self.rules = RulesEngine(modules, clock, thresholds)
        self.controller = LocalSafetyController(db, esp32, clock, settings, dispatcher)
        self.iot_client = IoTCommunicationClient(db, publisher)
        self.sensor_manager = SensorManager(db, clock, self.devices, self.rules,
                                            self.controller, settings.space_id)
        self.security = SecurityService(db, clock, self.rules, self.controller, self.iot_client,
                                       self.devices, vision, settings)
        self.security.recover()  # reinicio con una ventana abierta: timeout y escalamiento
        self.cloud_inbox = CloudInbox(self)
        if hasattr(publisher, "subscribe"):
            self.cloud_inbox.subscribe(publisher)

        # Hilo de camara solo con vision real (la que lee cuadros); el mock no lo necesita.
        self.camera_thread = (CameraThread(vision, self.security, clock, settings.camera_fps)
                              if hasattr(vision, "poll") else None)
        self._stop = threading.Event()
        self._thread = None

    def tick(self) -> None:
        """Entrada periodica: avanza las reglas dependientes del tiempo y envia el outbox."""
        with self.security.lock:  # no se cruza con una validacion o una intrusion en curso
            with self.db.session() as session:
                evaluations = self.rules.tick(session)
            for evaluation in evaluations:  # el controlador hace I/O: fuera de la transaccion
                self.controller.execute(evaluation.actions)   # primero se acciona
            if any(e.events for e in evaluations):
                with self.db.session() as session:
                    for evaluation in evaluations:
                        self.security.record_outcome(session, evaluation)
                        enqueue_events(session, evaluation.events, self.devices, self.clock,
                                       self.settings.space_id)
        self.iot_client.flush()

    def reset(self) -> list:
        """Demo: subsistemas al estado inicial y sirena, buzzer, ventilador y humidificador
        apagados, por las vias normales. Nunca desbloquea la cerradura."""
        log.info("REINICIO DE DEMO: subsistemas a su estado inicial")
        with self.security.lock:
            with self.db.session() as session:
                self.rules.reset(session)
            self.security.reset()
            return self.controller.execute([
                Action("siren", "off"), Action("buzzer", "off"),
                Action("fan", "set", {"duty": 0}), Action("humidifier", "off")])

    def start_background(self, interval: float = 1.0) -> None:
        """Hilo unico de tick (reglas + outbox) y, con vision real, el de camara. Una sola vez."""
        def loop():
            while not self._stop.wait(interval):
                try:
                    self.tick()
                except Exception:
                    log.exception("Error en el tick")
        self._thread = threading.Thread(target=loop, name="edge-tick", daemon=True)
        self._thread.start()
        if self.camera_thread is not None:
            self.camera_thread.start(self._stop)

    def stop(self) -> None:
        self._stop.set()


def create_app(settings, *, clock, esp32, publisher, vision, db=None, modules=None,
               dispatcher=None, start_background=False) -> Flask:
    """`dispatcher`: despachador de ordenes del LSC; sincrono por defecto (pruebas), con hilo
    trabajador en produccion (`build_default_app`)."""
    db = db or Database(settings.db_url)
    app = Flask("edge")
    edge = Edge(settings, clock, db, esp32, publisher, vision,
                modules if modules is not None else default_modules(), dispatcher)
    app.extensions["edge"] = edge
    app.register_blueprint(api.bp)
    if start_background:
        edge.start_background()
    return app


def build_default_app() -> Flask:
    """App de produccion: reloj, ESP32 y MQTT reales; vision segun PERSON_DETECTOR (mock por defecto)."""
    from dotenv import load_dotenv
    from edge.actuation.dispatcher import ThreadDispatcher
    from edge.actuation.esp32_client import RequestsEsp32Client
    from edge.communication.mqtt_publisher import PahoPublisher

    load_dotenv()
    configure_logging()
    settings = Settings.from_env(os.environ)
    publisher = PahoPublisher(settings.mqtt_broker_url, settings.mqtt_username,
                              settings.mqtt_password, settings.mqtt_tls)
    publisher.start()
    return create_app(settings, clock=SystemClock(), esp32=RequestsEsp32Client(),
                      publisher=publisher, vision=build_vision(settings),
                      dispatcher=ThreadDispatcher(), start_background=True)


def main() -> None:
    app = build_default_app()
    settings = app.extensions["edge"].settings
    app.run(host=settings.flask_host, port=settings.flask_port, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
