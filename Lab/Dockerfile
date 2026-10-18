FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY honeypot.py monitor.py dashboard.html test_monitor.py test_honeypot.py ./

EXPOSE 2323 8080
CMD ["python", "/app/monitor.py"]
