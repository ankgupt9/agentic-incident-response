from fastapi.testclient import TestClient

from services.alert_receiver.main import app

PAYLOAD = {"alerts": [{"status": "firing", "labels": {"alertname": "OrderSubmitErrorRateHigh"}}]}


def test_alert_is_recorded():
    client = TestClient(app)
    assert client.post("/alerts", json=PAYLOAD).json() == {"received": 1}
    assert client.get("/alerts/recent").json()[-1] == PAYLOAD
