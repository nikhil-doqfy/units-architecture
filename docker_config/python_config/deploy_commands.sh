#!/bin/bash

# Collect static files
echo "Collect static files"
python3 manage.py collectstatic --noinput

# Apply database migrations
echo "Apply database migrations"
python3 manage.py migrate


# Start server
echo "Starting server"
# python3 manage.py runserver 0.0.0.0:8000
# python3 manage.py runsslserver 0.0.0.0:8000 --certificate /certs/server.crt --key /certs/server.key
gunicorn property_management.wsgi:application --bind 0.0.0.0:8000 --timeout 120 --workers 5