web: gunicorn web.server:app --bind 0.0.0.0:${PORT:-8030} --workers 1 --threads 8 --timeout 600 --graceful-timeout 30 --access-logfile - --error-logfile -
