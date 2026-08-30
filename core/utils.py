import os
import requests
from django.conf import settings

def send_brevo_email(to_email, subject, html_content):
    api_key = os.getenv('BREVO_API_KEY')
    url = "https://api.brevo.com/v3/smtp/email"

    headers = {
        "accept": "application/json",
        "api-key": api_key,
        "content-type": "application/json"
    }

    payload = {
        "sender": {"email": "contrat@klassx.cloud", "name": "KLASSX"},
        "to": [{"email": to_email}],
        "subject": subject,
        "htmlContent": html_content
    }

    response = requests.post(url, json=payload, headers=headers)
    print("STATUT BREVO:", response.status_code)
    print("REPONSE BREVO:", response.text)
    return response.status_code == 201