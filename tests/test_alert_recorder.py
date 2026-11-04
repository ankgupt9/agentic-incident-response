from fastapi.testclient import TestClient

from simulator.alert_recorder.main import app

PAYLOAD = {"alerts": [{"status": "firing", "labels": {"alertname": "OrderSubmitErrorRateHigh"}}]}


def test_alert_is_recorded():
    client = TestClient(app)
    assert client.post("/alerts", json=PAYLOAD).json() == {"received": 1}
    assert client.get("/alerts/recent").json()[-1] == PAYLOAD
