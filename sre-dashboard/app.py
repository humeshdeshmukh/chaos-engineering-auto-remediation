import os
import yaml
import time
import logging
import requests
import json
from flask import Flask, render_template, jsonify, request

app = Flask(__name__)

# Configure Logging
import sys
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sre-dashboard")
logger.setLevel(logging.INFO)
sh = logging.StreamHandler(sys.stdout)
sh.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
logger.addHandler(sh)
logger.propagate = False # Prevent duplicate logs via root logger if Flask sets it up

# Configurable endpoints
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://prometheus-operator-kube-p-prometheus.monitoring.svc.cluster.local:9090")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")

# In-memory logs & alert states for UI display
evaluation_history = []
slack_alerts = []
chaos_active_experiments = {
    "network_delay": "Stopped",
    "pod_kill": "Stopped"
}

# Load Keptn files
def load_keptn_slo():
    path = "/app/keptn/slo.yaml"
    if not os.path.exists(path):
        # Fallback for local development/testing
        path = "keptn/slo.yaml"
        if not os.path.exists(path):
            path = "../keptn/slo.yaml"
            
    try:
        with open(path, 'r') as f:
            return yaml.safe_load(f)
    except Exception as e:
        logger.error(f"Error loading Keptn SLO file: {e}")
        return {}

def load_keptn_shipyard():
    path = "/app/keptn/shipyard.yaml"
    if not os.path.exists(path):
        path = "keptn/shipyard.yaml"
        if not os.path.exists(path):
            path = "../keptn/shipyard.yaml"
    try:
        with open(path, 'r') as f:
            return yaml.safe_load(f)
    except Exception as e:
        logger.error(f"Error loading Keptn Shipyard file: {e}")
        return {}

# Query Prometheus helper
def query_prometheus(query):
    logger.info(f"Querying Prometheus: {query}")
    try:
        response = requests.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": query}, timeout=3)
        logger.info(f"Prometheus response status code: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            results = data.get("data", {}).get("result", [])
            logger.info(f"Prometheus results count: {len(results)}")
            if results:
                val = results[0].get("value", [0, "0"])[1]
                logger.info(f"Prometheus parsed value: {val}")
                return float(val)
        else:
            logger.warn(f"Prometheus query failed with HTTP {response.status_code}: {response.text}")
        return 0.0
    except Exception as e:
        logger.error(f"Prometheus query failed with exception: {e}")
        return 0.0

# Keptn SLO Evaluator logic
def evaluate_slo_for_hash(canary_hash):
    slo_data = load_keptn_slo()
    if not slo_data:
        return "error", {"message": "SLO configuration not found"}
        
    objectives = slo_data.get("spec", {}).get("objectives", [])
    results = {}
    passed_all = True
    
    # We will build queries using the canary hash
    # If no canary_hash is provided, we query overall deployment job=payment-gateway
    hash_filter = f',pod=~"payment-gateway-{canary_hash}-.*"' if canary_hash else ''
    
    for obj in objectives:
        sli = obj.get("sli")
        pass_criteria = obj.get("pass", [{}])[0].get("criteria", [])
        
        val = 0.0
        status = "pass"
        criterion_str = pass_criteria[0] if pass_criteria else ""
        
        if sli == "response_time_p95":
            # Response time P95 in milliseconds
            query = f'histogram_quantile(0.95, sum(rate(payment_latency_seconds_bucket{{job="payment-gateway"{hash_filter}}}[1m])) by (le)) * 1000'
            val = query_prometheus(query)
            
            # Parse criterion like "<=250"
            if criterion_str.startswith("<="):
                threshold = float(criterion_str[2:])
                if val > threshold:
                    status = "fail"
                    passed_all = False
            elif criterion_str.startswith("<"):
                threshold = float(criterion_str[1:])
                if val >= threshold:
                    status = "fail"
                    passed_all = False
                    
        elif sli == "error_rate":
            # Error rate in percent (5xx / total)
            query_500 = f'sum(rate(payment_requests_total{{job="payment-gateway",status_code="500"{hash_filter}}}[1m]))'
            query_total = f'sum(rate(payment_requests_total{{job="payment-gateway"{hash_filter}}}[1m]))'
            
            val_500 = query_prometheus(query_500)
            val_total = query_prometheus(query_total)
            
            if val_total > 0:
                val = (val_500 / val_total) * 100
            else:
                val = 0.0
                
            if criterion_str.startswith("<="):
                threshold = float(criterion_str[2:])
                if val > threshold:
                    status = "fail"
                    passed_all = False
            elif criterion_str.startswith("<"):
                threshold = float(criterion_str[1:])
                if val >= threshold:
                    status = "fail"
                    passed_all = False
                    
        results[sli] = {
            "value": round(val, 2),
            "target": criterion_str,
            "status": status
        }
        
    result_status = "pass" if passed_all else "fail"
    
    # Store in history
    eval_log = {
        "timestamp": time.strftime('%Y-%m-%d %H:%M:%S'),
        "canary_hash": canary_hash or "all",
        "status": result_status,
        "results": results
    }
    evaluation_history.append(eval_log)
    if len(evaluation_history) > 50:
        evaluation_history.pop(0)
        
    # Trigger Slack notification if failed
    if result_status == "fail":
        send_slack_alert(canary_hash, results)
        
    return result_status, results

def send_slack_alert(canary_hash, results):
    timestamp = time.strftime('%H:%M:%S')
    alert_msg = {
        "id": f"alert-{int(time.time())}",
        "time": timestamp,
        "type": "rollback",
        "title": "🚨 CANARY DEPLOYMENT SLO VIOLATION & ROLLBACK TRIGGERED",
        "message": f"Canary hash *{canary_hash[:10]}* failed SLO validation rules. Initiating automated rollback sequence.",
        "details": []
    }
    for sli, res in results.items():
        status_emoji = "✅" if res["status"] == "pass" else "❌"
        alert_msg["details"].append(f"{status_emoji} *{sli}*: {res['value']} (Required: {res['target']})")
        
    slack_alerts.append(alert_msg)
    if len(slack_alerts) > 20:
        slack_alerts.pop(0)
        
    # If a real Slack webhook URL is configured, send it
    if SLACK_WEBHOOK_URL:
        try:
            payload = {
                "text": f"*{alert_msg['title']}*\n{alert_msg['message']}\n" + "\n".join(alert_msg["details"])
            }
            requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=2)
        except Exception as e:
            logger.error(f"Failed to post to real Slack Webhook: {e}")

@app.route("/")
def index():
    return render_template("index.html")

# Keptn evaluation endpoint for Argo Rollouts AnalysisTemplate
@app.route("/api/keptn/evaluate", methods=["GET"])
def evaluate():
    canary_hash = request.args.get("canary_hash", "")
    logger.info(f"Received Keptn SLO evaluation request for canary_hash={canary_hash}")
    
    result_status, details = evaluate_slo_for_hash(canary_hash)
    
    # Return 200 OK always. The response body is parsed by Argo's jsonPath.
    # Return pass/fail in the exact format required by AnalysisTemplate successCondition.
    return jsonify({
        "status": "success",
        "result": result_status,
        "details": details,
        "timestamp": time.time()
    })

# API for Web Dashboard
@app.route("/api/dashboard/state", methods=["GET"])
def dashboard_state():
    # 1. Get Rollout status
    rollout_info = {
        "name": "payment-gateway",
        "status": "Unknown",
        "step": "N/A",
        "replicas": 0,
        "updated_replicas": 0,
        "ready_replicas": 0,
        "stable_hash": "N/A",
        "canary_hash": "N/A"
    }
    pods = []
    
    try:
        import subprocess
        # Get rollout info
        res = subprocess.run(["kubectl", "get", "rollout", "payment-gateway", "-n", "default", "-o", "json"], capture_output=True, text=True)
        if res.returncode == 0:
            data = json.loads(res.stdout)
            status = data.get("status", {})
            rollout_info["status"] = status.get("phase", "Unknown")
            rollout_info["replicas"] = status.get("replicas", 0)
            rollout_info["updated_replicas"] = status.get("updatedReplicas", 0)
            rollout_info["ready_replicas"] = status.get("readyReplicas", 0)
            
            stable_rs = status.get("stableRS", "N/A")
            current_pod_hash = status.get("currentPodHash", "N/A")
            rollout_info["stable_hash"] = stable_rs[:10] if stable_rs else "N/A"
            if current_pod_hash and stable_rs and current_pod_hash != stable_rs:
                rollout_info["canary_hash"] = current_pod_hash[:10]
            else:
                rollout_info["canary_hash"] = "N/A"
            
            # Step progress
            steps = data.get("spec", {}).get("strategy", {}).get("canary", {}).get("steps", [])
            current_step_index = status.get("currentStepIndex", None)
            phase = status.get("phase", "")
            if current_step_index is not None and phase in ["Progressing", "Paused"] and current_step_index < len(steps):
                rollout_info["step"] = f"Step {current_step_index + 1}/{len(steps)}"
            else:
                rollout_info["step"] = "Healthy"
                
        # Get Pods info
        res_pods = subprocess.run(["kubectl", "get", "pods", "-n", "default", "-l", "app=payment-gateway", "-o", "json"], capture_output=True, text=True)
        if res_pods.returncode == 0:
            pod_data = json.loads(res_pods.stdout)
            for item in pod_data.get("items", []):
                name = item["metadata"]["name"]
                phase = item["status"]["phase"]
                labels = item["metadata"].get("labels", {})
                template_hash = labels.get("rollouts-pod-template-hash", "")
                
                # Identify role
                role = "stable"
                if template_hash and rollout_info["canary_hash"] and template_hash.startswith(rollout_info["canary_hash"]):
                    role = "canary"
                elif template_hash and rollout_info["stable_hash"] and template_hash.startswith(rollout_info["stable_hash"]):
                    role = "stable"
                    
                container_statuses = item["status"].get("containerStatuses", [])
                restarts = container_statuses[0].get("restartCount", 0) if container_statuses else 0
                state_str = phase
                if container_statuses:
                    state = container_statuses[0].get("state", {})
                    if "waiting" in state:
                        state_str = state["waiting"].get("reason", "Waiting")
                    elif "running" in state:
                        state_str = "Running"
                        
                pods.append({
                    "name": name,
                    "status": state_str,
                    "restarts": restarts,
                    "role": role,
                    "ip": item["status"].get("podIP", "N/A"),
                    "node": item["spec"].get("nodeName", "N/A")
                })
    except Exception as e:
        logger.error(f"Failed to fetch rollout/pod status: {e}")
        
    # 2. Get Live Prometheus metrics for dashboard charts
    p95_stable = query_prometheus('histogram_quantile(0.95, sum(rate(payment_latency_seconds_bucket{job="payment-gateway"}[1m])) by (le)) * 1000')
    error_rate_stable = query_prometheus('sum(rate(payment_requests_total{job="payment-gateway", status_code="500"}[1m])) / sum(rate(payment_requests_total{job="payment-gateway"}[1m])) * 100')
    throughput = query_prometheus('sum(rate(payment_requests_total{job="payment-gateway"}[1m]))')
    
    # 3. Check active Chaos Mesh experiments
    check_chaos_mesh_experiments()
    
    return jsonify({
        "rollout": rollout_info,
        "pods": pods,
        "metrics": {
            "p95_latency_ms": round(p95_stable, 1),
            "error_rate_percent": round(error_rate_stable, 1),
            "throughput_rps": round(throughput, 1)
        },
        "chaos_experiments": chaos_active_experiments,
        "evaluation_history": evaluation_history[-15:],
        "slack_alerts": slack_alerts[-10:]
    })

def check_chaos_mesh_experiments():
    import subprocess
    # Check if network latency chaos is active in default namespace
    try:
        res = subprocess.run(["kubectl", "get", "networkchaos", "-n", "default", "-o", "json"], capture_output=True, text=True)
        if res.returncode == 0:
            data = json.loads(res.stdout)
            items = data.get("items", [])
            active = False
            for item in items:
                status = item.get("status", {}).get("experiment", {}).get("phase", "Stopped")
                if status == "Injected":
                    active = True
                    break
            chaos_active_experiments["network_delay"] = "Running" if active else "Stopped"
        else:
            chaos_active_experiments["network_delay"] = "Stopped"
    except Exception:
        chaos_active_experiments["network_delay"] = "Stopped"
        
    # Check if pod kill chaos is active
    try:
        res = subprocess.run(["kubectl", "get", "podchaos", "-n", "default", "-o", "json"], capture_output=True, text=True)
        if res.returncode == 0:
            data = json.loads(res.stdout)
            items = data.get("items", [])
            active = False
            for item in items:
                status = item.get("status", {}).get("experiment", {}).get("phase", "Stopped")
                if status == "Injected":
                    active = True
                    break
            chaos_active_experiments["pod_kill"] = "Running" if active else "Stopped"
        else:
            chaos_active_experiments["pod_kill"] = "Stopped"
    except Exception:
        chaos_active_experiments["pod_kill"] = "Stopped"

# API to trigger Chaos Experiments manually
@app.route("/api/chaos/inject", methods=["POST"])
def inject_chaos():
    experiment_type = request.json.get("type")
    import subprocess
    
    # 1. Fetch current canary hash
    canary_hash = ""
    try:
        res_ro = subprocess.run(["kubectl", "get", "rollout", "payment-gateway", "-n", "default", "-o", "json"], capture_output=True, text=True)
        if res_ro.returncode == 0:
            ro_data = json.loads(res_ro.stdout)
            status = ro_data.get("status", {})
            stable_rs = status.get("stableRS", "")
            current_pod_hash = status.get("currentPodHash", "")
            if current_pod_hash and stable_rs and current_pod_hash != stable_rs:
                canary_hash = current_pod_hash
    except Exception as e:
        logger.error(f"Failed to fetch canary hash: {e}")
        
    if not canary_hash:
        return jsonify({"status": "error", "message": "Injection aborted: No active canary rollout step detected. Please trigger a deployment first."}), 400
        
    logger.info(f"Injecting chaos type={experiment_type} targeting canary_hash={canary_hash}")
    
    # 2. Load and render template
    yaml_filename = "network_delay.yaml" if experiment_type == "network_delay" else "pod_kill.yaml"
    path = f"/app/chaos/{yaml_filename}"
    if not os.path.exists(path):
        path = f"chaos/{yaml_filename}"
        if not os.path.exists(path):
            path = f"../chaos/{yaml_filename}"
            
    try:
        with open(path, 'r') as f:
            yaml_content = f.read()
            
        # Replace placeholder
        rendered_content = yaml_content.replace("CANARY_HASH", canary_hash)
        
        # Write to temp file
        temp_path = f"/tmp/chaos_{experiment_type}.yaml"
        with open(temp_path, 'w') as f:
            f.write(rendered_content)
            
        # Delete existing chaos mesh experiment first to prevent admission webhook update rejection
        subprocess.run(["kubectl", "delete", "-f", temp_path, "--ignore-not-found=true"], capture_output=True)
        # Apply the rendered manifest
        res = subprocess.run(["kubectl", "apply", "-f", temp_path], capture_output=True, text=True)
        if res.returncode == 0:
            chaos_active_experiments[experiment_type] = "Running"
            
            # Post to Slack alerts list
            if experiment_type == "network_delay":
                slack_alerts.append({
                    "id": f"alert-{int(time.time())}",
                    "time": time.strftime('%H:%M:%S'),
                    "type": "chaos_start",
                    "title": "⚡ CHAOS EXPERIMENT TRIGGERED: NETWORK DELAY",
                    "message": f"Chaos Mesh injected *200ms network delay (10ms jitter)* targeting payment-gateway-canary pods (hash: {canary_hash[:10]}).",
                    "details": []
                })
            else:
                slack_alerts.append({
                    "id": f"alert-{int(time.time())}",
                    "time": time.strftime('%H:%M:%S'),
                    "type": "chaos_start",
                    "title": "⚡ CHAOS EXPERIMENT TRIGGERED: POD KILL",
                    "message": f"Chaos Mesh scheduled *PodKill experiment* targeting payment-gateway-canary pods (hash: {canary_hash[:10]}).",
                    "details": []
                })
            return jsonify({"status": "success", "message": f"{experiment_type} injected successfully targeting canary pods."})
        else:
            return jsonify({"status": "error", "message": f"Failed to apply chaos manifest: {res.stderr}"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": f"Exception during injection: {str(e)}"}), 500

@app.route("/api/chaos/stop", methods=["POST"])
def stop_chaos():
    import subprocess
    errors = []
    
    # Delete network delay
    path_nd = "/app/chaos/network_delay.yaml"
    if not os.path.exists(path_nd): path_nd = "chaos/network_delay.yaml"
    if not os.path.exists(path_nd): path_nd = "../chaos/network_delay.yaml"
    res1 = subprocess.run(["kubectl", "delete", "-f", path_nd, "--ignore-not-found=true"], capture_output=True, text=True)
    if res1.returncode != 0:
        errors.append(res1.stderr)
        
    # Delete pod kill
    path_pk = "/app/chaos/pod_kill.yaml"
    if not os.path.exists(path_pk): path_pk = "chaos/pod_kill.yaml"
    if not os.path.exists(path_pk): path_pk = "../chaos/pod_kill.yaml"
    res2 = subprocess.run(["kubectl", "delete", "-f", path_pk, "--ignore-not-found=true"], capture_output=True, text=True)
    if res2.returncode != 0:
        errors.append(res2.stderr)
        
    chaos_active_experiments["network_delay"] = "Stopped"
    chaos_active_experiments["pod_kill"] = "Stopped"
    
    slack_alerts.append({
        "id": f"alert-{int(time.time())}",
        "time": time.strftime('%H:%M:%S'),
        "type": "chaos_stop",
        "title": "🛡️ ALL CHAOS EXPERIMENTS HALTED",
        "message": "All active Chaos Mesh experiments have been removed and deleted from the cluster.",
        "details": []
    })
    
    if errors:
        return jsonify({"status": "warning", "message": "Halted chaos but encountered some errors", "errors": errors})
    return jsonify({"status": "success", "message": "All chaos experiments stopped"})

# API to manually trigger a rollout promotion or abort
@app.route("/api/rollout/action", methods=["POST"])
def rollout_action():
    action = request.json.get("action")
    import subprocess
    
    if action == "promote":
        res = subprocess.run(["kubectl", "argo", "rollouts", "promote", "payment-gateway", "-n", "default"], capture_output=True, text=True)
        if res.returncode == 0:
            return jsonify({"status": "success", "message": "Rollout promoted manually"})
        return jsonify({"status": "error", "message": res.stderr})
        
    elif action == "abort":
        res = subprocess.run(["kubectl", "argo", "rollouts", "abort", "payment-gateway", "-n", "default"], capture_output=True, text=True)
        if res.returncode == 0:
            slack_alerts.append({
                "id": f"alert-{int(time.time())}",
                "time": time.strftime('%H:%M:%S'),
                "type": "rollback",
                "title": "🚨 MANUAL ROLLBACK TRIGGERED BY SRE",
                "message": "On-call engineer initiated a manual abort on rollout payment-gateway. Restoring stable build.",
                "details": []
            })
            return jsonify({"status": "success", "message": "Rollout aborted and rollback initiated"})
        return jsonify({"status": "error", "message": res.stderr})
        
    return jsonify({"status": "error", "message": "Invalid action"}), 400

@app.route("/api/rollout/update", methods=["POST"])
def update_rollout():
    import subprocess
    # Find current version
    res = subprocess.run(["kubectl", "get", "rollout", "payment-gateway", "-n", "default", "-o", "jsonpath={.spec.template.spec.containers[0].env[0].value}"], capture_output=True, text=True)
    current_version = res.stdout.strip() if res.returncode == 0 else "v1"
    
    new_version = "v2" if current_version == "v1" else "v1"
    
    logger.info(f"Triggering rollout update from {current_version} to {new_version}")
    
    # Use JSON patch to toggle env APP_VERSION on the custom rollout resource
    patch_json = '[{"op": "replace", "path": "/spec/template/spec/containers/0/env/0/value", "value": "' + new_version + '"}]'
    res_update = subprocess.run(["kubectl", "patch", "rollout", "payment-gateway", "-n", "default", "--type=json", "-p", patch_json], capture_output=True, text=True)
    
    if res_update.returncode == 0:
        # Clear slack_alerts to make it look clean for the new deployment run
        if len(slack_alerts) > 1:
            slack_alerts[:] = [slack_alerts[0]]
            
        slack_alerts.append({
            "id": f"alert-{int(time.time())}",
            "time": time.strftime('%H:%M:%S'),
            "type": "deployment_start",
            "title": f"🚀 NEW DEPLOYMENT TRIGGERED: {new_version.upper()}",
            "message": f"Progressive deployment of version *{new_version.upper()}* started. Shifted 25% traffic to canary pods.",
            "details": []
        })
        return jsonify({"status": "success", "message": f"Rollout updated to version {new_version.upper()} successfully!"})
    else:
        return jsonify({"status": "error", "message": res_update.stderr}), 500

if __name__ == "__main__":
    logger.info("Starting SRE Dashboard on port 5000...")
    app.run(host="0.0.0.0", port=5000)
