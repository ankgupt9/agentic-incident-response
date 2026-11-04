FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY simulator ./simulator
# Each compose service sets its own command, e.g. simulator.order_service.main:app
CMD ["uvicorn", "simulator.order_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
