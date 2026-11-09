"""Orquesta seguridad: servicios de sonido y persona -> reglas -> actuadores -> evidencia -> outbox.

Orden (AGENTS.md seccion 12): primero se acciona (sirena y cerradura), luego se guarda la
evidencia, se registra el evento y se encola la publicacion. Nunca al reves.
"""
import threading
import uuid

from edge.communication import outbox
from edge.security.person_detection import PersonDetectionService
from edge.security.sound_analysis import SoundAnalysisService
from edge.shared.clock import iso_utc
from edge.shared.logging import get_logger
from edge.shared.models import SecurityEventRow
from edge.rules_engine import RuleInput

log = get_logger("security")


class SecurityService:
    def __init__(self, db, clock, rules, controller, iot_client, devices, detector, settings):
        self.db = db
        self.clock = clock
        self.rules = rules
        self.controller = controller
        self.iot_client = iot_client
        self.devices = devices
        self.detector = detector
        self.settings = settings
        self.sound = SoundAnalysisService(clock, settings.sound_triggers_required,
                                          settings.sound_window_seconds)
        self.person = PersonDetectionService(detector, clock, rules.thresholds)
        self._lock = threading.RLock()

    # ---- entradas ----

    def on_sound_shot(self) -> bool:
        """Un disparo del SA92. Devuelve si el sonido es sospechoso."""
        with self._lock:
            suspicious = self.sound.record_shot()
            if suspicious:
                self._evaluate()
            return suspicious

    def process_frame(self) -> str | None:
        """Un cuadro de la camara (lo invoca el hilo de vision o la simulacion)."""
        with self._lock:
            outcome = self.person.process_frame()
            if outcome == "appeared":
                self._evaluate()
            return outcome

    def simulate_person(self, known: bool) -> str:
        """Inyecta una persona en el detector mock: aparece en un cuadro y se va en el siguiente."""
        with self._lock:
            self.detector.inject(known)
            outcome = self.process_frame()
            self.process_frame()
            return outcome or "none"

    # ---- correlacion y reaccion ----

    def _evaluate(self) -> None:
        extra = {"last_unknown_person_at": self.person.last_unknown_person_at,
                 "last_suspicious_sound_at": self.sound.last_suspicious_sound_at,
                 "event_id": str(uuid.uuid4())}
        with self.db.session() as session:
            evaluation = self.rules.handle_input(session, "security", "security", extra)
        self.controller.execute(evaluation.actions)           # 1. primero se acciona
        for event in evaluation.events:
            if event.metric_type == "intrusion":
                self._record_intrusion(event.data["eventId"], evaluation.state["state"])
        self.iot_client.flush()                               # 3. despues se publica

    def _record_intrusion(self, event_id: str, local_state: str) -> None:
        evidence_ref = self._save_evidence(event_id)
        now = iso_utc(self.clock.now_utc())
        device = self.devices.get("intrusion")
        with self.db.session() as session:
            session.add(SecurityEventRow(event_id=event_id, type="intrusion",
                                         evidence_path=evidence_ref, opened_at=now))
            if device is None:
                log.error("Falta el dispositivo 'intrusion' en devices.yaml: no se publica")
                return
            payload = {"deviceId": device.device_id, "metricType": "intrusion", "value": 1,
                       "unit": "event", "readingId": event_id, "eventId": event_id,
                       "measuredAt": now, "localState": local_state,
                       "evidenceRef": evidence_ref}
            outbox.enqueue(session, event_id,
                           f"sentrya/{self.settings.space_id}/{device.device_id}/readings",
                           payload, now)
        log.info("INTRUSION %s: sirena encendida, cerradura bloqueada, evidencia %s",
                 event_id, evidence_ref)

    def _save_evidence(self, event_id: str) -> str | None:
        try:
            image = self.detector.capture_evidence()
            directory = self.settings.evidence_dir
            directory.mkdir(parents=True, exist_ok=True)
            (directory / f"{event_id}.png").write_bytes(image)
            return f"evidence/{event_id}.png"
        except Exception:  # la evidencia nunca debe frenar la reaccion ni la publicacion
            log.exception("No se pudo guardar la evidencia")
            return None
