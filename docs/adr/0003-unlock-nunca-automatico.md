# La cerradura nunca se desbloquea sola

La cerradura queda bloqueada sin energía (`LOCK_ENERGIZE_TO_UNLOCK=true`) y ninguna regla local la desbloquea; solo una orden explícita. Por la misma razón, un `unlock` fallido no entra en la reconciliación por heartbeat: reintentarlo minutos después podría abrir la puerta cuando nadie lo espera. Un `lock` fallido sí se reintenta, porque es la dirección segura. Un lector podría esperar que todos los actuadores se reconcilien igual; esta excepción es deliberada.
