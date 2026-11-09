"""Arranque: factory de la app con adaptadores inyectables y entrada tick periodica."""
import os
import threading

from flask import Flask

from edge.actuation.local_safety_controller import LocalSafetyController
from edge.communication.events import enqueue_events
from edge.communication.iot_client import IoTCommunicationClient
from edge.config import thresholds as thresholds_config
from edge.config.devices import load_devices
from edge.config.settings import Settings
from edge.rules_engine import RulesEngine
from edge.sensors import api
from edge.sensors.sensor_manager import SensorManager
from edge.security import rules as security_rules
from edge.security.person_detection import MockPersonDetector
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

    def __init__(self, settings, clock, db, esp32, publisher, vision, modules):
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
        self.controller = LocalSafetyController(db, esp32, clock, settings)
        self.iot_client = IoTCommunicationClient(db, publisher)
        self.sensor_manager = SensorManager(db, clock, self.devices, self.rules,
                                            self.controller, settings.space_id)
        self.security = SecurityService(db, clock, self.rules, self.controller, self.iot_client,
                                       self.devices, vision, settings)
        self._stop = threading.Event()
        self._thread = None

    def tick(self) -> None:
        """Entrada periodica: avanza las reglas dependientes del tiempo y envia el outbox."""
        with self.db.session() as session:
            evaluations = self.rules.tick(session)
        for evaluation in evaluations:  # el controlador hace I/O: fuera de la transaccion
            self.controller.execute(evaluation.actions)   # primero se acciona
        if any(e.events for e in evaluations):
            with self.db.session() as session:
                for evaluation in evaluations:
                    enqueue_events(session, evaluation.events, self.devices, self.clock,
                                   self.settings.space_id)
        self.iot_client.flush()

    def start_background(self, interval: float = 1.0) -> None:
        """Hilo unico de tick (reglas + outbox). Llamar una sola vez."""
        def loop():
            while not self._stop.wait(interval):
                try:
                    self.tick()
                except Exception:
                    log.exception("Error en el tick")
        self._thread = threading.Thread(target=loop, name="edge-tick", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()


def create_app(settings, *, clock, esp32, publisher, vision, db=None, modules=None,
               start_background=False) -> Flask:
    db = db or Database(settings.db_url)
    app = Flask("edge")
    edge = Edge(settings, clock, db, esp32, publisher, vision,
                modules if modules is not None else default_modules())
    app.extensions["edge"] = edge
    app.register_blueprint(api.bp)
    if start_background:
        edge.start_background()
    return app


def build_default_app() -> Flask:
    """App de produccion: reloj, ESP32, MQTT y vision reales (vision mock por defecto)."""
    from dotenv import load_dotenv
    from edge.actuation.esp32_client import RequestsEsp32Client
    from edge.communication.mqtt_publisher import PahoPublisher

    load_dotenv()
    configure_logging()
    settings = Settings.from_env(os.environ)
    publisher = PahoPublisher(settings.mqtt_broker_url, settings.mqtt_username,
                              settings.mqtt_password, settings.mqtt_tls)
    publisher.start()
    return create_app(settings, clock=SystemClock(), esp32=RequestsEsp32Client(),
                      publisher=publisher, vision=MockPersonDetector(), start_background=True)


def main() -> None:
    app = build_default_app()
    settings = app.extensions["edge"].settings
    app.run(host=settings.flask_host, port=settings.flask_port, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
