#!/bin/sh
# Deploy the backend (FastAPI + agent + MCP) and the frontend (Django) to Cloud Run.
#
#   ./scripts/deploy-cloudrun.sh            # build, push and deploy both
#
# MongoDB stays on the VM. Both services reach it over the VPC at the VM's internal IP,
# so Mongo is never exposed to the internet. The backend only accepts traffic from inside
# the VPC (the frontend's calls go through the VPC); only the frontend is public.
#
# Secrets (GOOGLE_API_KEY, DJANGO_SECRET_KEY) are read from .env once and stored in
# Secret Manager. To change one later:
#   printf %s 'NEW_VALUE' | gcloud secrets versions add retailpilot-google-api-key --data-file=-
set -eu
cd "$(dirname "$0")/.."

PROJECT=${PROJECT:-$(gcloud config get-value project 2>/dev/null)}
REGION=${REGION:-asia-south1}         # same region as the VM
VM_IP=${VM_IP:-10.160.0.2}            # retailpilot-vm internal IP (Mongo on 27018)
REPO=retailpilot
REGISTRY=$REGION-docker.pkg.dev/$PROJECT/$REPO
TAG=$(git rev-parse --short HEAD)
MONGO_URI="mongodb://$VM_IP:27018"
env_value() { grep -E "^$1=" .env | tail -1 | cut -d= -f2-; }

echo "== Project $PROJECT, region $REGION, image tag $TAG"

# ---- one-time setup (safe to repeat) ----
gcloud services enable run.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com
gcloud artifacts repositories describe $REPO --location "$REGION" >/dev/null 2>&1 \
  || gcloud artifacts repositories create $REPO --repository-format docker --location "$REGION"
# Lets the frontend reach the backend's *.run.app URL through the VPC (internal-only backend).
gcloud compute networks subnets update default --region "$REGION" --enable-private-ip-google-access

RUNTIME_SA="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')-compute@developer.gserviceaccount.com"
for pair in retailpilot-google-api-key:GOOGLE_API_KEY retailpilot-django-secret:DJANGO_SECRET_KEY; do
  secret=${pair%%:*}; var=${pair#*:}
  if ! gcloud secrets describe "$secret" >/dev/null 2>&1; then
    value=$(env_value "$var")
    [ -n "$value" ] || { echo "$var is empty in .env"; exit 1; }
    printf %s "$value" | gcloud secrets create "$secret" --data-file=- --replication-policy automatic
  fi
  gcloud secrets add-iam-policy-binding "$secret" --member "serviceAccount:$RUNTIME_SA" \
    --role roles/secretmanager.secretAccessor >/dev/null
done

# ---- build and push ----
gcloud auth configure-docker "$REGION-docker.pkg.dev" --quiet
for target in backend frontend; do
  docker build --target $target -t "$REGISTRY/$target:$TAG" .
  docker push "$REGISTRY/$target:$TAG"
done

COMMON_ENV="MONGO_URI=$MONGO_URI,MONGO_DB=$(env_value MONGO_DB),STALE_HOURS=$(env_value STALE_HOURS)"

# ---- backend: private, reachable only from the VPC ----
gcloud run deploy retailpilot-backend \
  --image "$REGISTRY/backend:$TAG" --region "$REGION" \
  --network default --subnet default --vpc-egress private-ranges-only \
  --ingress internal --allow-unauthenticated \
  --set-env-vars "$COMMON_ENV,CHAT_DB=$(env_value CHAT_DB),GEMINI_MODEL=$(env_value GEMINI_MODEL)" \
  --set-secrets GOOGLE_API_KEY=retailpilot-google-api-key:latest \
  --memory 1Gi --cpu 1 --timeout 600 --max-instances 3

BACKEND_URL=$(gcloud run services describe retailpilot-backend --region "$REGION" --format 'value(status.url)')

# ---- frontend: public website ----
gcloud run deploy retailpilot-frontend \
  --image "$REGISTRY/frontend:$TAG" --region "$REGION" \
  --network default --subnet default --vpc-egress all-traffic \
  --ingress all --allow-unauthenticated \
  --set-env-vars "$COMMON_ENV,BACKEND_URL=$BACKEND_URL,DJANGO_DEBUG=0,DJANGO_ALLOWED_HOSTS=.run.app" \
  --set-secrets DJANGO_SECRET_KEY=retailpilot-django-secret:latest \
  --memory 1Gi --cpu 1 --timeout 600 --max-instances 3

echo
echo "Backend (internal): $BACKEND_URL"
echo "Website:            $(gcloud run services describe retailpilot-frontend --region "$REGION" --format 'value(status.url)')"
