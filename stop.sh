#!/usr/bin/env bash

# ==============================================================================
# Chaos Engineering & Auto-Remediation Platform Clean up Script
# ==============================================================================

# Terminal Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

echo -e "${CYAN}======================================================================${NC}"
echo -e "${CYAN}      CLEANING UP CHAOS ENGINEERING PLATFORM RESOURCES               ${NC}"
echo -e "${CYAN}======================================================================${NC}"

# Define script and project directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

# ------------------------------------------------------------------------------
# STEP 1: Terminate Traffic Generator
# ------------------------------------------------------------------------------
echo -e "${YELLOW}Stopping payment traffic generator deployment...${NC}"
kubectl delete -f kubernetes/traffic-generator.yaml --ignore-not-found=true
echo -e "${GREEN}[OK] Traffic generator stopped.${NC}"

# ------------------------------------------------------------------------------
# STEP 2: Delete Active Chaos Experiments
# ------------------------------------------------------------------------------
echo -e "${YELLOW}Cleaning up active Chaos Mesh experiments...${NC}"
kubectl delete networkchaos --all -n default &>/dev/null || true
kubectl delete podchaos --all -n default &>/dev/null || true
echo -e "${GREEN}[OK] Chaos experiments cleaned up.${NC}"

# ------------------------------------------------------------------------------
# STEP 3: Delete Kubernetes Resources
# ------------------------------------------------------------------------------
echo -e "${YELLOW}Deleting workload and dashboard resources...${NC}"
kubectl delete -f kubernetes/sre-dashboard.yaml --ignore-not-found=true
kubectl delete -f kubernetes/rollouts/rollout.yaml --ignore-not-found=true
kubectl delete -f kubernetes/rollouts/analysis_template.yaml --ignore-not-found=true
kubectl delete -f kubernetes/servicemonitor.yaml --ignore-not-found=true
kubectl delete -f kubernetes/service.yaml --ignore-not-found=true
kubectl delete configmap keptn-configs chaos-configs --ignore-not-found=true
echo -e "${GREEN}[OK] Platform resources deleted.${NC}"

# ------------------------------------------------------------------------------
# STEP 4: Optional Helm Clean up Prompt
# ------------------------------------------------------------------------------
echo -e "\n${YELLOW}Would you like to uninstall Prometheus, Argo Rollouts, and Chaos Mesh operators? (y/N)${NC}"
# Read input with timeout to allow automated environments to skip teardown of operators
read -t 5 -n 1 -r REPLY || REPLY="N"
echo ""

if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo -e "${YELLOW}Uninstalling Helm charts (prometheus, argo-rollouts, chaos-mesh)...${NC}"
    helm uninstall prometheus-operator --namespace monitoring || true
    helm uninstall argo-rollouts --namespace argo-rollouts || true
    helm uninstall chaos-mesh --namespace chaos-mesh || true
    
    kubectl delete namespace monitoring argo-rollouts chaos-mesh --ignore-not-found=true || true
    echo -e "${GREEN}[OK] Helm operators removed.${NC}"
else
    echo -e "${GREEN}Helm operators (Prometheus, Argo Rollouts, Chaos Mesh) left running in cluster.${NC}"
fi

echo -e "\n${GREEN}======================================================================${NC}"
echo -e "${GREEN}      TEARDOWN COMPLETED SUCCESSFULLY                                 ${NC}"
echo -e "${GREEN}======================================================================${NC}"
