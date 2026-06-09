import os
import time
import random
import logging
from flask import Flask, request, jsonify
from prometheus_client import start_http_server, Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

app = Flask(__name__)

# Configure Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# Prometheus Metrics
PAYMENT_REQUESTS = Counter(
    'payment_requests_total',
    'Total number of payment requests',
    ['method', 'endpoint', 'status_code', 'version']
)
PAYMENT_LATENCY = Histogram(
    'payment_latency_seconds',
    'Payment processing latency in seconds',
    ['method', 'endpoint', 'version'],
    buckets=(0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)
)

# Read environment variables
APP_VERSION = os.getenv("APP_VERSION", "v1")
DB_HOST = os.getenv("DB_HOST", "localhost")

@app.route('/pay', methods=['GET', 'POST'])
def pay():
    start_time = time.time()
    method = request.method
    endpoint = '/pay'
    
    # Simulate processing time (20ms - 50ms)
    processing_time = random.uniform(0.02, 0.05)
    
    # Check if simulation triggers errors or extra delays
    trigger_error = request.args.get('error', 'false').lower() == 'true'
    inject_delay = request.args.get('delay', '0')
    
    try:
        delay_val = float(inject_delay)
        if delay_val > 0:
            processing_time += delay_val
    except ValueError:
        pass

    time.sleep(processing_time)
    
    if trigger_error or (random.random() < 0.01): # 1% baseline error rate
        status_code = 500
        response = jsonify({"status": "error", "message": "Internal Database Timeout", "version": APP_VERSION})
    else:
        status_code = 200
        response = jsonify({"status": "success", "transaction_id": str(random.randint(100000, 999999)), "version": APP_VERSION})
        
    duration = time.time() - start_time
    PAYMENT_REQUESTS.labels(method=method, endpoint=endpoint, status_code=status_code, version=APP_VERSION).inc()
    PAYMENT_LATENCY.labels(method=method, endpoint=endpoint, version=APP_VERSION).observe(duration)
    
    logger.info(f"Processed payment: method={method} status={status_code} duration={duration:.4f}s version={APP_VERSION}")
    return response, status_code

@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        "status": "healthy",
        "version": APP_VERSION,
        "database": f"connected to {DB_HOST}"
    }), 200

@app.route('/metrics', methods=['GET'])
def metrics():
    # Expose custom prometheus metrics
    return generate_latest(), 200, {'Content-Type': CONTENT_TYPE_LATEST}

if __name__ == '__main__':
    # Listen on port 8080
    logger.info(f"Starting payment-gateway version {APP_VERSION} on port 8080...")
    app.run(host='0.0.0.0', port=8080)
