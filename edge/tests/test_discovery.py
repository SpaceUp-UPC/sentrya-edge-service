import json
import socket

from edge.config.settings import Settings
from edge.sensors.discovery import DiscoveryBeacon, beacon_payload


def test_payload_identifica_al_edge_y_su_puerto_http():
    assert json.loads(beacon_payload(5000)) == {"service": "sentrya-edge", "port": 5000}


def test_discovery_port_configurable_y_apagable():
    assert Settings.from_env({}).discovery_port == 5001
    assert Settings.from_env({"DISCOVERY_PORT": "0"}).discovery_port == 0


def test_el_anuncio_llega_a_quien_escucha_en_el_puerto():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.settimeout(2)
        port = listener.getsockname()[1]
        # Por loopback se envia directo (sin broadcast) para que la prueba no dependa de la red.
        beacon = DiscoveryBeacon(port, 5000, addresses=lambda: {"127.0.0.1"})
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.sendto(beacon.payload, ("127.0.0.1", port))
        data, _ = listener.recvfrom(1024)
    assert json.loads(data)["service"] == "sentrya-edge"


def test_una_interfaz_que_falla_no_detiene_el_anuncio():
    beacon = DiscoveryBeacon(5001, 5000, addresses=lambda: {"203.0.113.9"})  # IP ajena: bind falla
    assert beacon.send_once() == set()
