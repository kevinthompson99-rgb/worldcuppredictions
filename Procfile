web: gunicorn run:app --workers 1
release: flask db upgrade && flask seed-admin
