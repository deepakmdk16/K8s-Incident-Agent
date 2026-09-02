#!/usr/bin/env bash
# cluster.sh — create the authoring cluster evals/inject.sh expects.
#
# Nothing else in the tree recorded how the cluster the frozen fixtures were
# captured on was built, so a cold session had to reconstruct it: inject.sh
# just assumes the context kind-incident-lab exists. It is a single-node kind
# cluster with kindnet, storageClass standard and no metrics-server — the
# authoring contract's environment (evals/scenarios/README.md rule 3).
#
# The node image is PINNED BY DIGEST, not tag: every fixture records its own
# cluster/version.json, and a case captured on a different Kubernetes version
# is not comparable with the set. This digest is v1.37.0, the version every
# committed fixture was captured on.
#
# Usage: cluster.sh [--name <n>] [--delete]
set -u

NODE_IMAGE="kindest/node@sha256:a1ed56cfb0e7b93589bdf97c8cd566405a265939e3620fc4f5de89adff580ae5"
WORKLOAD_IMAGE="busybox:1.36"
NAME="incident-lab"
DELETE=0

die() { echo "cluster: $*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --name) NAME="$2"; shift 2 ;;
    --delete) DELETE=1; shift ;;
    *) die "unknown arg: $1 (usage: [--name N] [--delete])" ;;
  esac
done

command -v kind >/dev/null 2>&1 || die "kind not on PATH"
command -v docker >/dev/null 2>&1 || die "docker not on PATH"
docker info >/dev/null 2>&1 || die "docker daemon is not running"

if [ "$DELETE" -eq 1 ]; then
  kind delete cluster --name "$NAME"
  exit 0
fi

if kind get clusters 2>/dev/null | grep -qx "$NAME"; then
  echo "cluster: $NAME already exists — nothing to do"
else
  echo "cluster: creating $NAME on pinned node image"
  kind create cluster --name "$NAME" --image "$NODE_IMAGE" || die "create failed"
fi

# Scenario workloads run on a node-cached image so no pull can throttle mid
# capture and write 'toomanyrequests' into a fixture instead of the injected
# fault (authoring contract rule 3). `kind load docker-image` is NOT used here:
# it exits 0 while importing nothing (docs/failure-modes.md, 2026-09-02).
if docker exec "${NAME}-control-plane" crictl images 2>/dev/null | grep -q busybox; then
  echo "cluster: $WORKLOAD_IMAGE already cached in the node"
else
  echo "cluster: importing $WORKLOAD_IMAGE into the node"
  TAR="$(mktemp -t busybox-XXXXXX).tar"
  docker pull "$WORKLOAD_IMAGE" >/dev/null || die "pull failed"
  docker save "$WORKLOAD_IMAGE" -o "$TAR" || die "save failed"
  docker cp "$TAR" "${NAME}-control-plane:/busybox.tar" || die "cp failed"
  docker exec "${NAME}-control-plane" ctr -n k8s.io images import /busybox.tar >/dev/null \
    || die "ctr import failed"
  rm -f "$TAR"
  docker exec "${NAME}-control-plane" crictl images 2>/dev/null | grep -q busybox \
    || die "$WORKLOAD_IMAGE still not in the node after import"
fi

echo "cluster: ready — context kind-${NAME}"
kubectl --context "kind-${NAME}" get nodes
