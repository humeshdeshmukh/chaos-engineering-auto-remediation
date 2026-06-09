FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir flask prometheus_client

COPY app.py .

EXPOSE 8080

ENV APP_VERSION=v1
ENV DB_HOST=postgres-db

CMD ["python", "app.py"]
