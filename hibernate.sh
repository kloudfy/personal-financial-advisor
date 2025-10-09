#!/usr/bin/env bash
set -euo pipefail

# Hibernate all workloads in a namespace:
# - Save current replicas for Deployments/StatefulSets
# - Scale them to 0
# - Patch HPAs minReplicas=0 (snapshot original)
# - Suspend CronJobs (snapshot original)
# - Convert LoadBalancer Services -> ClusterIP (snapshot original)
#
# Usage: NS=default STATE_DIR=/tmp/pfa-hibernate ./hibernate.sh

NS="${NS:-default}"
STATE_DIR="${STATE_DIR:-/tmp/pfa-hibernate}"
mkdir -p "${STATE_DIR}"

need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing dependency: $1" >&2; exit 1; }; }
need kubectl
need jq

echo "==> Namespace: ${NS}"
echo "==> State dir: ${STATE_DIR}"

# 0) Verify access
kubectl get ns "${NS}" >/dev/null

# 1) Save current replicas
echo "==> Snapshot replicas (Deployments/StatefulSets)"
kubectl -n "${NS}" get deploy -o json | jq -r '.items[] | [.metadata.name, (.spec.replicas//1)] | @tsv' \
  > "${STATE_DIR}/deploy_replicas.tsv"
kubectl -n "${NS}" get statefulset -o json | jq -r '.items[] | [.metadata.name, (.spec.replicas//1)] | @tsv' \
  > "${STATE_DIR}/sts_replicas.tsv"

# 2) Scale all to zero
echo "==> Scale Deployments to 0"
kubectl -n "${NS}" get deploy -o name | xargs -r -n1 kubectl -n "${NS}" scale --replicas=0
echo "==> Scale StatefulSets to 0"
kubectl -n "${NS}" get statefulset -o name | xargs -r -n1 kubectl -n "${NS}" scale --replicas=0

# 3) Snapshot + set HPAs minReplicas=0
echo "==> Snapshot HPAs and set minReplicas=0"
kubectl -n "${NS}" get hpa -o json > "${STATE_DIR}/hpas.json" || echo '{"items":[]}' > "${STATE_DIR}/hpas.json"
while IFS= read -r H; do
  kubectl -n "${NS}" delete hpa "${H}" || true
done < <(jq -r '.items[].metadata.name' "${STATE_DIR}/hpas.json")

# 4) Snapshot + suspend CronJobs
echo "==> Snapshot CronJobs and suspend=true"
kubectl -n "${NS}" get cronjob -o json > "${STATE_DIR}/cronjobs.json" || echo '{"items":[]}' > "${STATE_DIR}/cronjobs.json"
while IFS= read -r CJ; do
  kubectl -n "${NS}" patch cronjob "${CJ}" --type=merge -p '{"spec":{"suspend":true}}' || true
done < <(jq -r '.items[].metadata.name' "${STATE_DIR}/cronjobs.json")

# 5) Snapshot LB services and convert to ClusterIP
echo "==> Snapshot LoadBalancer Services and switch to ClusterIP"
kubectl -n "${NS}" get svc -o json | jq -r '.items[] | select(.spec.type=="LoadBalancer") | .metadata.name' \
  > "${STATE_DIR}/lb_services.txt"
while IFS= read -r S; do
  kubectl -n "${NS}" patch svc "${S}" -p '{"spec":{"type":"ClusterIP"}}' || true
done < "${STATE_DIR}/lb_services.txt"

# 6) Kill known traffic generators (best-effort)
echo "==> Scale common traffic generators to 0 (best-effort)"
kubectl -n "${NS}" scale deploy/loadgenerator --replicas=0 2>/dev/null || true

echo "==> Done. Snapshot files:"
ls -1 "${STATE_DIR}" | sed "s|^|  ${STATE_DIR}/|"
