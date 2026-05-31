from django.core.management.base import BaseCommand
from django.conf import settings

from survey.services.nextcloud import NextcloudStorage


class Command(BaseCommand):
    help = "Vérifie la connexion à Nextcloud (WebDAV)."

    def handle(self, *args, **options):
        self.stdout.write(f"NEXTCLOUD_ENABLED = {settings.NEXTCLOUD_ENABLED}")
        self.stdout.write(f"NEXTCLOUD_URL = {settings.NEXTCLOUD_URL}")
        self.stdout.write(f"NEXTCLOUD_USER = {settings.NEXTCLOUD_USER}")

        if not settings.NEXTCLOUD_PASSWORD:
            self.stderr.write(self.style.ERROR("NEXTCLOUD_PASSWORD est vide."))
            return

        nc = NextcloudStorage()
        if nc.ping():
            self.stdout.write(self.style.SUCCESS("Nextcloud accessible (status.php OK)."))
        else:
            self.stderr.write(self.style.ERROR("Impossible de joindre Nextcloud."))
