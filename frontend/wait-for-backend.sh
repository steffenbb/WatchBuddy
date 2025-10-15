#!/bin/sh
# Wait for backend DNS to resolve, then for backend to be reachable before starting nginx
until getent hosts backend; do
  echo "Waiting for backend DNS to resolve..."
  sleep 2
done
until nc -z backend 8000; do
  echo "Waiting for backend to be available..."
  sleep 2
done
exec nginx -g 'daemon off;'
