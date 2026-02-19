#!/bin/bash
# Start the app in the background
PORT=8080 bash "$(dirname "$0")/run.sh" &

# Wait until the server is actually responding (up to 3 minutes)
echo "⏳ Waiting for app to start…"
for i in $(seq 1 90); do
  if curl -s http://localhost:8080 > /dev/null 2>&1; then
    echo "✅ App is ready!"
    open http://localhost:8080
    exit 0
  fi
  sleep 2
done

# Fallback — open anyway after timeout
open http://localhost:8080
