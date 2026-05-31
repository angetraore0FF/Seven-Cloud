from django.core.management.base import BaseCommand
from django.conf import settings

from survey.models import Fichier
from survey.services.nextcloud import NextcloudStorage, NextcloudError, build_remote_path


class Command(BaseCommand):
    help = "Migre les fichiers locaux vers Nextcloud."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Simulation sans écriture')

    def handle(self, *args, **options):
        if not settings.NEXTCLOUD_ENABLED:
            self.stderr.write(self.style.ERROR('Activez NEXTCLOUD_ENABLED=true dans .env'))
            return

        nc = NextcloudStorage()
        if not nc.ping():
            self.stderr.write(self.style.ERROR('Nextcloud injoignable. Lancez: python manage.py check_nextcloud'))
            return

        qs = Fichier.objects.filter(stockage_externe=False).exclude(fichier='')
        total = qs.count()
        self.stdout.write(f'{total} fichier(s) à migrer')

        for fichier in qs.select_related('proprietaire', 'proprietaire__profile', 'proprietaire__profile__pme'):
            profile = getattr(fichier.proprietaire, 'profile', None)
            if not profile:
                self.stderr.write(f'Skip {fichier.pk}: pas de profil')
                continue
            remote_path = build_remote_path(fichier.proprietaire, profile, fichier.nom)
            if options['dry_run']:
                self.stdout.write(f'  [dry] {fichier.nom} -> {remote_path}')
                continue
            try:
                with fichier.fichier.open('rb') as src:
                    meta = nc.upload(remote_path, src)
                fichier.nextcloud_path = meta['path']
                fichier.nextcloud_etag = meta.get('etag', '')
                fichier.stockage_externe = True
                fichier.fichier.delete(save=False)
                fichier.save()
                self.stdout.write(self.style.SUCCESS(f'  OK {fichier.nom}'))
            except NextcloudError as exc:
                self.stderr.write(self.style.ERROR(f'  ERREUR {fichier.nom}: {exc}'))
