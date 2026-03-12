#!/bin/bash

echo "=============================="
echo "RUN PROGRAM"
echo "=============================="

# Start python
python app.py &
PY_PID=$!

# Start npm
npm run dev &
NPM_PID=$!

echo ""
echo "🚀 Apps running"
echo "Press CTRL+C to stop"
echo ""

cleanup() {
  echo ""
  echo "🛑 Stopping services..."
  kill -TERM $PY_PID 2>/dev/null
  kill -TERM $NPM_PID 2>/dev/null
  wait
  echo "✅ All stopped"
  exit 0
}

trap cleanup INT TERM

wait
