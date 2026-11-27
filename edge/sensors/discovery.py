"""Anuncio del edge en la red local para que las ESP32 encuentren su IP solas.

Cada `interval` segundos se envia un broadcast UDP con {"service": "sentrya-edge", "port": <http>}
por cada interfaz IPv4 de la laptop. La ESP32 toma como IP del edge la de origen del paquete.
Asi, si la IP de la laptop cambia (otro hotspot, otra red), no hay que volver a subir el firmware.
El edge solo envia: no hace falta abrir puertos de entrada en el firewall.
"""
import json
import socket
import threading

from edge.shared.logging import get_logger

log = get_logger("discovery")

SERVICE = "sentrya-edge"


def beacon_payload(http_port: int) -> bytes:
    return json.dumps({"service": SERVICE, "port": http_port}).encode()


def local_ipv4_addresses() -> set:
    """IPs IPv4 de la laptop (sin loopback). Incluye la de la ruta por defecto."""
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    try:  # IP de la interfaz con ruta por defecto (no envia paquetes)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("10.255.255.255", 1))
            ips.add(probe.getsockname()[0])
    except OSError:
        pass
    return {ip for ip in ips if not ip.startswith("127.")}


class DiscoveryBeacon:
    def __init__(self, discovery_port: int, http_port: int, interval: float = 2.0,
                 addresses=local_ipv4_addresses):
        self.discovery_port = discovery_port
        self.payload = beacon_payload(http_port)
        self.interval = interval
        self.addresses = addresses
        self._stop = threading.Event()
        self._announced = set()

    def send_once(self) -> set:
        """Un broadcast por interfaz (en Windows, ligar a la IP elige la interfaz de salida)."""
        sent = set()
        for ip in sorted(self.addresses()) or [""]:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                    sock.bind((ip, 0))
                    sock.sendto(self.payload, ("255.255.255.255", self.discovery_port))
                    sent.add(ip)
            except OSError:
                continue
        if sent - self._announced:  # se loguea solo cuando aparece una IP nueva
            log.info("Anunciando el edge por UDP %s desde %s", self.discovery_port,
                     ", ".join(sorted(sent)) or "la interfaz por defecto")
        self._announced = sent
        return sent

    def start(self) -> None:
        def loop():
            while not self._stop.is_set():
                self.send_once()
                self._stop.wait(self.interval)
        threading.Thread(target=loop, name="edge-discovery", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
