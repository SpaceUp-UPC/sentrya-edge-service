"""Cliente ESP32 (adaptador): POST http://<ip>/command. Se completa en el ticket del Local Safety Controller."""
import requests


class RequestsEsp32Client:
    def send_command(self, ip: str, body: dict) -> dict:
        response = requests.post(f"http://{ip}/command", json=body, timeout=2)
        response.raise_for_status()
        return response.json()
