# Conexpro / JBD / Xiaoxiang BMS → MQTT bridge.
# Bleak (BlueZ DBus) inside a container needs network_mode: host + DBus mount.
FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        bluez \
        dbus \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY jbd_protocol.py bms_bridge.py ./

ENV PYTHONUNBUFFERED=1

CMD ["python", "-u", "bms_bridge.py"]
