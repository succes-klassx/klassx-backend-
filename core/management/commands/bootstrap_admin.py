"""
Crée (ou met à jour le mot de passe d')un compte admin, sans prompt
interactif — pensé pour tourner automatiquement dans la commande de
build d'un hébergeur (Render, etc.) qui ne donne pas d'accès Shell sur
le plan gratuit, contrairement à `createsuperuser` qui exige un
terminal interactif.

Lit DJANGO_SUPERUSER_EMAIL / DJANGO_SUPERUSER_USERNAME /
DJANGO_SUPERUSER_PASSWORD dans l'environnement. Idempotent — si le
compte existe déjà (même email), met juste à jour son mot de passe et
s'assure qu'il est bien admin/staff/superuser, au lieu d'échouer. Sûr à
relancer à chaque déploiement.
"""
import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

User = get_user_model()


class Command(BaseCommand):
    help = "Crée ou met à jour le compte admin à partir des variables d'environnement DJANGO_SUPERUSER_*. Ne fait rien si elles ne sont pas définies (pour ne jamais bloquer un build)."

    def handle(self, *args, **options):
        email = os.environ.get("DJANGO_SUPERUSER_EMAIL")
        username = os.environ.get("DJANGO_SUPERUSER_USERNAME")
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD")

        if not (email and username and password):
            self.stdout.write(self.style.WARNING(
                "DJANGO_SUPERUSER_EMAIL/USERNAME/PASSWORD non définis — rien à faire."
            ))
            return

        user, created = User.objects.get_or_create(
            email=email,
            defaults={"username": username, "role": User.Role.ADMIN},
        )
        user.username = username
        user.role = User.Role.ADMIN
        user.is_staff = True
        user.is_superuser = True
        user.set_password(password)
        user.save()

        if created:
            self.stdout.write(self.style.SUCCESS(f"Compte admin créé : {email}"))
        else:
            self.stdout.write(self.style.SUCCESS(f"Compte admin déjà existant, mot de passe mis à jour : {email}"))
