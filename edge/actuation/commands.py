"""Ordenes explicitas (demo o nube): una sola via hacia el Local Safety Controller y las reglas."""
from edge.sensors.normalizers import ValidationError


def run_command(edge, device_key, action, params) -> dict:
    """Ejecuta una orden. Lanza ValidationError si es invalida. Devuelve el cuerpo de respuesta."""
    if not isinstance(params, dict):
        raise ValidationError("params debe ser un objeto")
    if action == "mode":   # AUTO/MANUAL: lo resuelve el modulo de reglas, no el actuador
        mode = params.get("mode")
        try:
            with edge.lock, edge.db.session() as session:
                known = edge.rules.set_mode(session, device_key, mode)
        except ValueError as error:
            raise ValidationError(str(error)) from None
        if not known:
            raise ValidationError(f"{device_key} no admite el modo")
        return {"deviceKey": device_key, "mode": mode}
    result = edge.controller.send(device_key, action, params)
    return {"commandId": result.command_id, "status": result.status,
            "simulated": result.simulated}
