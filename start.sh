#!/usr/bin/env bash

# ==============================================================================
# Chaos Engineering & Auto-Remediation Platform Setup Script
# ==============================================================================

set -e

# Terminal Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

echo -e "${CYAN}======================================================================${NC}"
echo -e "${CYAN}      DEPLOYING CHAOS ENGINEERING & AUTO-REMEDIATION PLATFORM          ${NC}"
echo -e "${CYAN}======================================================================${NC}"

# Define script and project directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

# Ensure local bin exists and add to path
mkdir -p bin
export PATH="${SCRIPT_DIR}/bin:$PATH"

# Helper function to print steps
print_step() {
    echo -e "\n${BLUE}>>> [STEP] $1...${NC}"
}

# ------------------------------------------------------------------------------
# STEP 1: Verify Kubernetes & Minikube
# ------------------------------------------------------------------------------
print_step "1/10: Verifying Kubernetes cluster status"
PROFILE="multi-tenant-platform"
if ! minikube status -p "${PROFILE}" &>/dev/null; then
    echo -e "${RED}[ERROR] Minikube profile '${PROFILE}' is not running.${NC}"
    exit 1
fi
echo -e "${GREEN}[OK] Cluster is online.${NC}"

# ------------------------------------------------------------------------------
# STEP 2: Configure Helm Repos
# ------------------------------------------------------------------------------
print_step "2/10: Adding Helm repositories"
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo add argo https://argoproj.github.io/argo-helm
helm repo add chaos-mesh https://charts.chaos-mesh.org
helm repo update
echo -e "${GREEN}[OK] Helm repositories configured.${NC}"

# ------------------------------------------------------------------------------
# STEP 3: Deploy Kube-Prometheus-Stack
# ------------------------------------------------------------------------------
print_step "3/10: Deploying Prometheus and Grafana operator"
helm upgrade --install prometheus-operator prometheus-community/kube-prometheus-stack \
    --namespace monitoring \
    --create-namespace \
    -f kubernetes/prometheus-values.yaml \
    --wait
echo -e "${YELLOW}Patching Prometheus and Grafana services to NodePort for external access...${NC}"
kubectl patch svc prometheus-operator-grafana -n monitoring -p '{"spec": {"type": "NodePort"}}'
kubectl patch svc prometheus-operator-kube-p-prometheus -n monitoring -p '{"spec": {"type": "NodePort"}}'
echo -e "${GREEN}[OK] Prometheus and Grafana deployed and exposed successfully.${NC}"

# ------------------------------------------------------------------------------
# STEP 4: Deploy Argo Rollouts
# ------------------------------------------------------------------------------
print_step "4/10: Deploying Argo Rollouts controller"
helm upgrade --install argo-rollouts argo/argo-rollouts \
    --namespace argo-rollouts \
    --create-namespace \
    --wait
echo -e "${GREEN}[OK] Argo Rollouts controller deployed successfully.${NC}"

# ------------------------------------------------------------------------------
# STEP 5: Deploy Chaos Mesh
# ------------------------------------------------------------------------------
print_step "5/10: Deploying Chaos Mesh engine"
helm upgrade --install chaos-mesh chaos-mesh/chaos-mesh \
    --namespace chaos-mesh \
    --create-namespace \
    --set chaosDaemon.runtime=docker \
    --set chaosDaemon.socketPath=/var/run/docker.sock \
    --wait
echo -e "${GREEN}[OK] Chaos Mesh engine deployed successfully.${NC}"

# ------------------------------------------------------------------------------
# STEP 6: Install kubectl-argo-rollouts locally on host
# ------------------------------------------------------------------------------
print_step "6/10: Downloading Argo Rollouts kubectl plugin"
if [ ! -f bin/kubectl-argo-rollouts ]; then
    echo -e "${YELLOW}Downloading plugin binary...${NC}"
    curl -sLO https://github.com/argoproj/argo-rollouts/releases/latest/download/kubectl-argo-rollouts-linux-amd64
    chmod +x kubectl-argo-rollouts-linux-amd64
    mv kubectl-argo-rollouts-linux-amd64 bin/kubectl-argo-rollouts
fi
echo -e "${GREEN}[OK] Argo Rollouts plugin installed at $(pwd)/bin/kubectl-argo-rollouts${NC}"

# ------------------------------------------------------------------------------
# STEP 7: Build & Load Application Images
# ------------------------------------------------------------------------------
print_step "7/10: Building and loading Docker images"
echo -e "${YELLOW}Building payment-gateway image...${NC}"
docker build -t payment-gateway:latest ./payment-gateway

echo -e "${YELLOW}Loading payment-gateway into Minikube...${NC}"
minikube -p "${PROFILE}" image load payment-gateway:latest

echo -e "${YELLOW}Building sre-dashboard image...${NC}"
docker build -t sre-dashboard:latest ./sre-dashboard

echo -e "${YELLOW}Loading sre-dashboard into Minikube...${NC}"
minikube -p "${PROFILE}" image load sre-dashboard:latest
echo -e "${GREEN}[OK] Images built and loaded successfully.${NC}"

# ------------------------------------------------------------------------------
# STEP 8: Create ConfigMaps for Keptn and Chaos Templates
# ------------------------------------------------------------------------------
print_step "8/10: Syncing configuration templates into cluster ConfigMaps"
kubectl create configmap keptn-configs --from-file=keptn/ --dry-run=client -o yaml | kubectl apply -f -
kubectl create configmap chaos-configs --from-file=chaos/ --dry-run=client -o yaml | kubectl apply -f -
echo -e "${GREEN}[OK] ConfigMaps deployed.${NC}"

# ------------------------------------------------------------------------------
# STEP 9: Apply Workload and Dashboard Resources
# ------------------------------------------------------------------------------
print_step "9/10: Deploying services, monitors, rollouts, and dashboards"
kubectl apply -f kubernetes/service.yaml
kubectl apply -f kubernetes/servicemonitor.yaml
kubectl apply -f kubernetes/rollouts/analysis_template.yaml
kubectl apply -f kubernetes/rollouts/rollout.yaml
kubectl apply -f kubernetes/sre-dashboard.yaml

echo -e "${YELLOW}Waiting for SRE Dashboard rollout...${NC}"
kubectl rollout status deployment/sre-dashboard -n default --timeout=60s
echo -e "${GREEN}[OK] SRE Dashboard is online!${NC}"

# ------------------------------------------------------------------------------
# STEP 10: Start Traffic Load Generator
# ------------------------------------------------------------------------------
print_step "10/10: Launching synthetic payment load generator pod"
kubectl apply -f kubernetes/traffic-generator.yaml
echo -e "${GREEN}[OK] Traffic generator deployment created successfully.${NC}"

# ------------------------------------------------------------------------------
# SETUP COMPLETE
# ------------------------------------------------------------------------------
# Resolve Minikube IP
MINIKUBE_IP=$(minikube -p "${PROFILE}" ip || echo "192.168.58.2")

# Resolve NodePorts
SRE_PORT=$(kubectl get svc sre-dashboard -n default -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null || echo "30500")
PAY_PORT=$(kubectl get svc payment-gateway -n default -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null || echo "30501")
CHAOS_PORT=$(kubectl get svc chaos-dashboard -n chaos-mesh -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null || echo "31655")
GRAFANA_PORT=$(kubectl get svc prometheus-operator-grafana -n monitoring -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null || echo "30441")
PROM_PORT=$(kubectl get svc prometheus-operator-kube-p-prometheus -n monitoring -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null || echo "31412")
ARGOCD_PORT=$(kubectl get svc argocd-server -n argocd -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null || echo "30792")

# Retrieve Passwords & Tokens
GRAFANA_PASS=$(kubectl --namespace monitoring get secrets prometheus-operator-grafana -o jsonpath="{.data.admin-password}" | base64 -d 2>/dev/null || echo "Not Found")
ARGOCD_PASS=$(kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d 2>/dev/null || echo "Not Found")
CHAOS_TOKEN=$(kubectl create token chaos-dashboard -n chaos-mesh --duration=8760h 2>/dev/null || echo "Not Generated")

echo -e "\n${GREEN}======================================================================${NC}"
echo -e "${GREEN}      CHAOS ENGINEERING PLATFORM SETUP SUCCESSFUL                     ${NC}"
echo -e "${GREEN}======================================================================${NC}"
echo -e "\n${YELLOW}Access Information, URLs & Credentials:${NC}"
echo -e "----------------------------------------------------------------------"
echo -e "${CYAN}1. SRE Control Center & Dashboard (Web UI)${NC}"
echo -e "   - Direct URL: http://${MINIKUBE_IP}:${SRE_PORT}"
echo -e "   - Port Forward (Backup): kubectl port-forward svc/sre-dashboard 5000:5000"
echo -e ""
echo -e "${CYAN}2. Chaos Mesh Dashboard${NC}"
echo -e "   - Direct URL: http://${MINIKUBE_IP}:${CHAOS_PORT}"
echo -e "   - Authentication Token: ${CHAOS_TOKEN}"
echo -e "   - Port Forward (Backup): kubectl port-forward -n chaos-mesh svc/chaos-dashboard 2333:2333"
echo -e ""
echo -e "${CYAN}3. Argo CD Web UI${NC}"
echo -e "   - Direct URL: http://${MINIKUBE_IP}:${ARGOCD_PORT} (HTTP) / https://${MINIKUBE_IP}:32541 (HTTPS)"
echo -e "   - Credentials: Username: admin | Password: ${ARGOCD_PASS}"
echo -e "   - Port Forward (Backup): kubectl port-forward -n argocd svc/argocd-server 8080:80"
echo -e ""
echo -e "${CYAN}4. Grafana Console (Metrics Dashboards)${NC}"
echo -e "   - Direct URL: http://192.168.58.2:${GRAFANA_PORT}"
echo -e "   - Credentials: Username: admin | Password: ${GRAFANA_PASS}"
echo -e "   - Port Forward (Backup): kubectl port-forward -n monitoring svc/prometheus-operator-grafana 3000:80"
echo -e ""
echo -e "${CYAN}5. Prometheus Raw Console${NC}"
echo -e "   - Direct URL: http://${MINIKUBE_IP}:${PROM_PORT}"
echo -e "   - Port Forward (Backup): kubectl port-forward -n monitoring svc/prometheus-operator-kube-p-prometheus 9090:9090"
echo -e ""
echo -e "${CYAN}6. Payment Gateway API NodePort${NC}"
echo -e "   - Direct URL: http://${MINIKUBE_IP}:${PAY_PORT}/pay"
echo -e "----------------------------------------------------------------------\n"
