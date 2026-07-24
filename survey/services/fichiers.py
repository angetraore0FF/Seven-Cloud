from django.conf import settings

from ..models import Fichier
from .nextcloud import NextcloudStorage, NextcloudError, build_remote_path
from .quota import verifier_quota_upload


def upload_fichier(user, uploaded_file, profile):
    """Enregistre un fichier localement ou sur Nextcloud selon le mode choisi par l'utilisateur."""
    nom = uploaded_file.name
    taille = uploaded_file.size
    type_mime = getattr(uploaded_file, "content_type", "") or ""

    verifier_quota_upload(profile, taille)

    if profile.utilise_stockage_distance():
        try:
            remote_path = build_remote_path(user, profile, nom)
            nc = NextcloudStorage()
            meta = nc.upload(remote_path, uploaded_file)
            return Fichier.objects.create(
                nom=nom,
                proprietaire=user,
                taille=taille,
                type_mime=type_mime,
                chiffre=True,
                nextcloud_path=meta["path"],
                nextcloud_etag=meta.get("etag", ""),
                stockage_externe=True,
            )
        except NextcloudError as exc:
            raise ValueError(str(exc)) from exc

    return Fichier.objects.create(
        nom=nom,
        fichier=uploaded_file,
        proprietaire=user,
        taille=taille,
        type_mime=type_mime,
        chiffre=True,
    )


def delete_fichier_storage(fichier):
    """Supprime le binaire (local ou Nextcloud) avant suppression du modèle."""
    if fichier.stockage_externe and fichier.nextcloud_path and settings.NEXTCLOUD_ENABLED:
        try:
            NextcloudStorage().delete(fichier.nextcloud_path)
        except NextcloudError:
            pass
