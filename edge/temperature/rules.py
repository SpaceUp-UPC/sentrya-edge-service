"""Modulo de reglas de temperatura (Nodo 3).

Tracer bullet: solo el estado NORMAL. WARNING/CRITICAL, histeresis y confirmacion
por lecturas consecutivas llegan en el ticket de temperatura completa.
"""

NAME = "temperature"


class TemperatureRules:
    name = NAME
    metric_types = ("temperature",)

    def initial_state(self) -> dict:
        return {"state": "NORMAL"}

    def evaluate(self, input, state, clock):
        return dict(state), [], []


MODULE = TemperatureRules()
