#!/usr/bin/env sh
set -e

# /app/logs is normally a bind mount, so its ownership reflects the host
# directory, not the image. Fix it here (as root) before dropping to
# appuser, so the container works regardless of host UID/GID.
chown -R appuser:appuser /app/logs

echo "Running database migrations..."
gosu appuser alembic upgrade head

echo "Starting application..."
exec gosu appuser uvicorn app.main:app --host 0.0.0.0 --port 8000
