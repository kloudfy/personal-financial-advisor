#!/usr/bin/env bash
set -euo pipefail

# Wake workloads back up from snapshots saved by hibernate.sh:
# - Restore Deployments/StatefulSets replica counts
# - Restore HPA minReplicas
# - Unsuspend CronJobs that were previously not suspended
# - Convert ClusterIP Services (that used to be LB) back to LoadBalancer
#
# Usage: NS=default STATE_DIR=/tmp/pfa-hibernate ./wake.sh

NS="${NS:-default}"
STATE_DIR="${STATE_DIR:-/tmp/pfa-hibernate}"

need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing dependency: $1" >&2; exit 1; }; }
need kubectl
need jq

echo "==> Namespace: ${NS}"
echo "==> State dir: ${STATE_DIR}"

# 0) Verify access
kubectl get ns "${NS}" >/dev/null

# 1) Restore Deployments
if [[ -f "${STATE_DIR}/deploy_replicas.tsv" ]]; then
  echo "==> Restoring Deployments replicas"
  while IFS=$'\t' read -r name replicas; do
    [[ -n "${name}" ]] || continue
    replicas="${replicas:-1}"
    kubectl -n "${NS}" scale deploy/"${name}" --replicas="${replicas}" || true
  done < "${STATE_DIR}/deploy_replicas.tsv"
else
  echo "WARN: No deployment snapshot found at ${STATE_DIR}/deploy_replicas.tsv"
fi

# 2) Restore StatefulSets
if [[ -f "${STATE_DIR}/sts_replicas.tsv" ]]; then
  echo "==> Restoring StatefulSets replicas"
  while IFS=$'\t' read -r name replicas; do
    [[ -n "${name}" ]] || continue
    replicas="${replicas:-1}"
    kubectl -n "${NS}" scale statefulset/"${name}" --replicas="${replicas}" || true
  done < "${STATE_DIR}/sts_replicas.tsv"
else
  echo "WARN: No statefulset snapshot found at ${STATE_DIR}/sts_replicas.tsv"
fi

# 3) Restore HPA minReplicas
if [[ -f "${STATE_DIR}/hpas.json" ]]; then
  echo "==> Restoring HPA minReplicas"
  while IFS=$'\t' read -r name minr; do
    [[ -n "${name}" ]] || continue
    kubectl -n "${NS}" patch hpa "${name}" --type='json' -p="[ {\"op\":\"replace\",\"path\":\"/spec/minReplicas\",\"value\":${minr} } ]" || true
  done < <(jq -r '.items[] | [.metadata.name, (.spec.minReplicas // 1)] | @tsv' "${STATE_DIR}/hpas.json")
else
  echo "WARN: No HPA snapshot found at ${STATE_DIR}/hpas.json"
fi

# 4) Unsuspend CronJobs that were previously unsuspended
if [[ -f "${STATE_DIR}/cronjobs.json" ]]; then
  echo "==> Restoring CronJobs suspend flags (only those previously false)"
  while IFS= read -r cj; do
    kubectl -n "${NS}" patch cronjob "${cj}" --type=merge -p '{"spec":{"suspend":false}}' || true
  done < <(jq -r '.items[] | select((.spec.suspend // false) == false) | .metadata.name' "${STATE_DIR}/cronjobs.json")
else
  echo "WARN: No CronJob snapshot found at ${STATE_DIR}/cronjobs.json"
fi

# 5) Recreate LoadBalancer Services
if [[ -f "${STATE_DIR}/lb_services.txt" ]]; then
  echo "==> Switching Services back to LoadBalancer"
  while IFS= read -r s; do
    kubectl -n "${NS}" patch svc "${s}" -p '{"spec":{"type":"LoadBalancer"}}' || true
  done < "${STATE_DIR}/lb_services.txt"
else
  echo "WARN: No LB Service snapshot found at ${STATE_DIR}/lb_services.txt"
fi

echo "==> Done. Check status:"
kubectl -n "${NS}" get deploy,sts,svc
echo "Tip: run your smokes (e.g., 'make smoke-fast') once pods are Ready."
