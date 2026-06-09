FROM python:3.11-slim

# Install system dependencies including curl, kubectl, and kubectl-argo-rollouts
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl" \
    && chmod +x kubectl \
    && mv kubectl /usr/local/bin/ \
    && curl -LO https://github.com/argoproj/argo-rollouts/releases/latest/download/kubectl-argo-rollouts-linux-amd64 \
    && chmod +x kubectl-argo-rollouts-linux-amd64 \
    && mv kubectl-argo-rollouts-linux-amd64 /usr/local/bin/kubectl-argo-rollouts \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir flask requests pyyaml

COPY app.py .
COPY templates/ templates/

# Expose port 5000 for the Flask server
EXPOSE 5000

ENV PYTHONUNBUFFERED=1

CMD ["python", "app.py"]
