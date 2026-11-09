"""Simulador de las 3 ESP32 de la maqueta para probar el edge sin hardware.

Levanta un servidor HTTP por nodo que responde `POST /command` (aplica la orden y
contesta `simulated: true`) y envia un heartbeat al edge cada pocos segundos con la
IP y el estado de sus actuadores, igual que el firmware propuesto en AGENTS.md seccion 8.

Uso:
    python tools/fake_esp32.py                       # edge en http://127.0.0.1:5000
    python tools/fake_esp32.py --edge http://192.168.1.10:5000 --heartbeat 5

Solo usa la biblioteca estandar.
"""
import argparse
import json
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

NODES = {
    "node-1": {"port": 8081, "actuators": {"siren": "off", "lock": "locked"}},
    "node-2": {"port": 8082, "actuators": {"humidifier": "off"}},
    "node-3": {"port": 8083, "actuators": {"fan": 0, "buzzer": "off", "valve": "closed",
                                           "sampling": 30}},
}
STATE_AFTER = {
    ("siren", "on"): "on", ("siren", "off"): "off",
    ("humidifier", "on"): "on", ("humidifier", "off"): "off",
    ("buzzer", "on"): "on", ("buzzer", "off"): "off",
    ("lock", "lock"): "locked", ("lock", "unlock"): "unlocked",
    ("valve", "open"): "open", ("valve", "close"): "closed",
}
lock = threading.Lock()


def apply(node: str, body: dict) -> dict:
    actuator, action = body.get("actuator"), body.get("action")
    params = body.get("params") or {}
    actuators = NODES[node]["actuators"]
    with lock:
        if actuator not in actuators:
            return {"commandId": body.get("commandId"), "status": "FAILED", "simulated": True}
        if actuator == "fan":
            actuators["fan"] = params.get("duty", 0)
        elif actuator == "sampling":
            actuators["sampling"] = params.get("intervalSeconds")
        else:
            actuators[actuator] = STATE_AFTER.get((actuator, action), actuators[actuator])
    if (actuator, action) == ("lock", "unlock"):
        pulse = float(params.get("pulseSeconds", 3))
        threading.Timer(pulse, lambda: _relock(node)).start()  # la cerradura vuelve a bloquear sola
    print(f"[{node}] {actuator} {action} {params or ''} -> {actuators.get(actuator)}", flush=True)
    return {"commandId": body.get("commandId"), "status": "APPLIED", "simulated": True}


def _relock(node: str) -> None:
    with lock:
        NODES[node]["actuators"]["lock"] = "locked"
    print(f"[{node}] lock -> locked (fin del pulso)", flush=True)


def make_handler(node: str):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != "/command":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                self.send_error(400)
                return
            payload = json.dumps(apply(node, body)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):  # silencia el log por peticion
            pass

    return Handler


def heartbeat_loop(edge: str, host: str, every: float) -> None:
    started = time.monotonic()
    while True:
        for node, info in NODES.items():
            with lock:
                actuators = dict(info["actuators"])
            actuators.pop("sampling", None)
            body = json.dumps({"ip": f"{host}:{info['port']}", "actuators": actuators,
                               "uptime": int(time.monotonic() - started)}).encode()
            request = urllib.request.Request(f"{edge}/api/v1/nodes/{node}/heartbeat", data=body,
                                             headers={"Content-Type": "application/json"})
            try:
                urllib.request.urlopen(request, timeout=3).read()
            except OSError as error:
                print(f"[{node}] heartbeat fallido: {error}", flush=True)
        time.sleep(every)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--edge", default="http://127.0.0.1:5000", help="URL base del edge")
    parser.add_argument("--host", default="127.0.0.1",
                        help="IP con la que el edge alcanza a este simulador")
    parser.add_argument("--heartbeat", type=float, default=10, help="segundos entre heartbeats")
    args = parser.parse_args()
    for node, info in NODES.items():
        server = ThreadingHTTPServer(("0.0.0.0", info["port"]), make_handler(node))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        print(f"[{node}] escuchando /command en el puerto {info['port']}", flush=True)
    try:
        heartbeat_loop(args.edge.rstrip("/"), args.host, args.heartbeat)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
