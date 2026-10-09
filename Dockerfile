FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini ./
COPY migrations/ ./migrations/
COPY app/ ./app/

# NOTE: this container runs as root because the Docker backend (single-device installs) needs the Docker socket.
# On Kubernetes the Deployment in deploy/k8s runs it as a non-root user.
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
