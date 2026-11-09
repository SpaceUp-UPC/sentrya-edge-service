"""Orquesta seguridad: servicios de sonido y persona -> reglas -> actuadores -> evidencia -> outbox.

Orden (AGENTS.md seccion 12): primero se acciona (sirena y cerradura), luego se guarda la
evidencia, se registra el evento y se encola la publicacion. Nunca al reves.
"""
import threading
import uuid
from datetime import timedelta

from edge.communication.events import enqueue_events
from edge.security.person_detection import PersonDetectionService
from edge.security.sound_analysis import SoundAnalysisService
from edge.shared.clock import iso_utc
from edge.shared.logging import get_logger
from edge.shared.models import SecurityEventRow
from edge.rules_engine import Event
from edge.security.rules import INTRUSION, NAME as SECURITY
from edge.shared import state_store

log = get_logger("security")


class SecurityService:
    def __init__(self, db, clock, rules, controller, iot_client, devices, detector, settings,
                 lock=None):
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
        self._lock = lock or threading.RLock()

    @property
    def lock(self):
        """El lock del edge: serializa las entradas de seguridad con el tick y las lecturas."""
        return self._lock

    # ---- entradas ----

    def on_sound_shot(self) -> bool:
        """Un disparo del SA92. Devuelve si el sonido es sospechoso."""
        with self._lock:
            suspicious = self.sound.record_shot()
            if suspicious:
                self._evaluate()
            return suspicious

    def process_frame(self) -> str | None:
        """Un cuadro de la camara (lo invoca el hilo de vision o la simulacion).

        La inferencia (DNN) corre fuera del lock del edge; solo la entrega a las reglas lo toma.
        """
        observations = self.detector.detect()
        with self._lock:
            outcome = self.person.observe(observations)
            if outcome == "appeared":
                self._evaluate()
            return outcome

    def simulate_person(self, known: bool) -> str:
        """Inyecta una persona en el detector mock: aparece en un cuadro y se va en el siguiente."""
        self.detector.inject(known)
        outcome = self.process_frame()
        self.process_frame()
        return outcome or "none"

    def validate(self, decision: str, event_id: str | None = None) -> dict | None:
        """Respuesta del usuario (demo o MQTT). None si no hay una ventana abierta que responda."""
        with self._lock:
            with self.db.session() as session:
                open_id = self._open_event_id(session)
                if open_id is None or (event_id is not None and event_id != open_id):
                    return None
                evaluation = self.rules.handle_input(
                    session, SECURITY, "validation", {"decision": decision, "event_id": open_id})
            self.controller.actuate(evaluation)  # primero se acciona
            with self.db.session() as session:
                if decision == "reject":
                    self._close(session, open_id, "rejected")
                    log.info("Intrusion %s rechazada: sirena apagada, MONITORING "
                             "(la cerradura sigue bloqueada)", open_id)
                self.record_outcome(session, evaluation)
                enqueue_events(session, evaluation.events, self.devices, self.clock,
                               self.settings.space_id)
            self.iot_client.flush()
            return {"eventId": open_id, "decision": decision,
                    "state": evaluation.state["state"]}

    def reset(self) -> None:
        """Demo: olvida sonidos y personas recientes y cierra la ventana abierta sin escalar.

        El estado de las reglas lo reinicia el Rules Engine; los actuadores, el controlador.
        """
        with self._lock:
            self.sound = SoundAnalysisService(self.clock, self.settings.sound_triggers_required,
                                              self.settings.sound_window_seconds)
            self.person = PersonDetectionService(self.detector, self.clock, self.rules.thresholds)
            with self.db.session() as session:
                for row in session.query(SecurityEventRow).filter(
                        SecurityEventRow.closed_at.is_(None)).all():
                    self._close(session, row.event_id, "reset")

    def recover(self) -> None:
        """Al arrancar: todo evento abierto se cierra como timeout y escala; no se retoma la ventana.

        El reloj monotonico del proceso anterior no sirve, por eso se usa security_events
        (con su deadline en UTC) y no el estado de las reglas.
        """
        with self._lock:
            with self.db.session() as session:
                evaluation = self.rules.handle_input(session, SECURITY, "restart", {})
            self.controller.actuate(evaluation)  # primero se acciona
            with self.db.session() as session:
                rows = session.query(SecurityEventRow).filter(
                    SecurityEventRow.closed_at.is_(None)).all()
                events = [Event("escalation", "escalation", {
                    "eventId": row.event_id, "reason": "timeout",
                    "localState": evaluation.state["state"]}) for row in rows]
                for row in rows:
                    self._close(session, row.event_id, "timeout")
                    log.info("Reinicio con la intrusion %s abierta (deadline %s): se cierra "
                             "como timeout y se escala", row.event_id, row.deadline_at)
                enqueue_events(session, events, self.devices, self.clock, self.settings.space_id)

    @staticmethod
    def _open_event_id(session) -> str | None:
        state, _ = state_store.load_state(session, SECURITY)
        if not state or state.get("state") != INTRUSION:
            return None
        return state["event_id"]

    # ---- correlacion y reaccion ----

    def _evaluate(self) -> None:
        correlation = {"last_unknown_person_at": self.person.last_unknown_person_at,
                       "last_suspicious_sound_at": self.sound.last_suspicious_sound_at,
                       "event_id": str(uuid.uuid4())}
        with self.db.session() as session:
            evaluation = self.rules.handle_input(session, SECURITY, "security", correlation)
        self.controller.actuate(evaluation)  # 1. primero se acciona
        for event in evaluation.events:
            if event.metric_type == "intrusion":
                self._record_intrusion(event.data["eventId"], evaluation.state["state"])
        self.iot_client.flush()                               # 3. despues se publica

    def _record_intrusion(self, event_id: str, local_state: str) -> None:
        evidence_ref = self._save_evidence(event_id)
        now_utc = self.clock.now_utc()
        window = self.rules.thresholds["security"]["validation_window_seconds"]
        # El deadline en UTC solo sirve para reconocer la ventana tras un reinicio; la ventana
        # en si la vence el reloj monotonico de las reglas.
        deadline = iso_utc(now_utc + timedelta(seconds=window))
        with self.db.session() as session:
            session.add(SecurityEventRow(event_id=event_id, type="intrusion",
                                         evidence_path=evidence_ref, opened_at=iso_utc(now_utc),
                                         deadline_at=deadline))
            enqueue_events(session, [Event("intrusion", "intrusion", {
                "eventId": event_id, "localState": local_state, "evidenceRef": evidence_ref})],
                self.devices, self.clock, self.settings.space_id)
        log.info("INTRUSION %s: sirena encendida, cerradura bloqueada, evidencia %s",
                 event_id, evidence_ref)

    # ---- cierre de la ventana ----

    def record_outcome(self, session, evaluation) -> None:
        """Cierra en security_events la ventana que resolvio esta evaluacion (confirm/timeout).

        Lo llama `Edge.tick` (timeout) y `validate` (confirm), en la misma transaccion en que
        se encola el escalamiento.
        """
        if evaluation.subsystem != SECURITY:
            return
        for event in evaluation.events:
            if event.metric_type == "escalation":
                self._close(session, event.data["eventId"], event.data["reason"])
                log.info("ESCALAMIENTO %s (%s): estado %s", event.data["eventId"],
                         event.data["reason"], evaluation.state["state"])

    def _close(self, session, event_id: str, validation: str) -> None:
        row = session.get(SecurityEventRow, event_id)
        if row is not None and row.closed_at is None:
            row.validation = validation
            row.closed_at = iso_utc(self.clock.now_utc())

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
