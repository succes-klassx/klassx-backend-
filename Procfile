web: gunicorn klassx.wsgi --log-file -
release: python manage.py migrate && python manage.py seed_subjects
