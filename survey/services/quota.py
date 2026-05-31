from django.db.models import Sum

from ..models import Fichier, UserProfile
from ..utils.storage import GB, format_bytes, go_to_bytes, pourcentage


def _fichiers_actifs_qs():
    return Fichier.objects.filter(est_corbeille=False)


def octets_utilisateur(user) -> int:
    return (
        _fichiers_actifs_qs()
        .filter(proprietaire=user)
        .aggregate(total=Sum("taille"))["total"]
        or 0
    )


def octets_pme(pme) -> int:
    if not pme:
        return 0
    return (
        _fichiers_actifs_qs()
        .filter(proprietaire__profile__pme=pme)
        .aggregate(total=Sum("taille"))["total"]
        or 0
    )


def verifier_quota_upload(profile: UserProfile, taille_fichier: int) -> None:
    """Lève ValueError si le quota utilisateur ou PME est dépassé."""
    taille_fichier = int(taille_fichier or 0)
    user = profile.user
    utilise = octets_utilisateur(user)
    quota_user = go_to_bytes(profile.quota_stockage)
    if utilise + taille_fichier > quota_user:
        reste = max(0, quota_user - utilise)
        raise ValueError(
            f"Quota personnel dépassé ({format_bytes(utilise)} / {profile.quota_stockage} Go). "
            f"Disponible : {format_bytes(reste)}, fichier : {format_bytes(taille_fichier)}."
        )

    pme = profile.pme
    if pme:
        utilise_pme = octets_pme(pme)
        quota_pme = go_to_bytes(pme.quota_stockage)
        if utilise_pme + taille_fichier > quota_pme:
            reste = max(0, quota_pme - utilise_pme)
            raise ValueError(
                f"Quota entreprise « {pme.nom} » dépassé "
                f"({format_bytes(utilise_pme)} / {pme.quota_stockage} Go). "
                f"Disponible : {format_bytes(reste)}. Contactez l'administrateur."
            )


def stats_stockage_utilisateur(profile: UserProfile) -> dict:
    """Statistiques pour jauges interface utilisateur."""
    utilise = octets_utilisateur(profile.user)
    quota_go = profile.quota_stockage
    quota_octets = go_to_bytes(quota_go)
    data = {
        "used_bytes": utilise,
        "used_label": format_bytes(utilise),
        "quota_gb": quota_go,
        "quota_label": f"{quota_go} Go",
        "pourcentage_utilise": pourcentage(utilise, quota_octets),
        "reste_label": format_bytes(max(0, quota_octets - utilise)),
    }
    pme = profile.pme
    if pme:
        utilise_pme = octets_pme(pme)
        quota_pme_octets = go_to_bytes(pme.quota_stockage)
        data.update({
            "pme": pme,
            "pme_nom": pme.nom,
            "pme_used_bytes": utilise_pme,
            "pme_used_label": format_bytes(utilise_pme),
            "pme_quota_gb": pme.quota_stockage,
            "pme_pourcentage": pourcentage(utilise_pme, quota_pme_octets),
            "pme_reste_label": format_bytes(max(0, quota_pme_octets - utilise_pme)),
        })
    return data


def stats_stockage_pme(pme) -> dict:
    utilise = octets_pme(pme)
    quota_octets = go_to_bytes(pme.quota_stockage)
    return {
        "used_bytes": utilise,
        "used_label": format_bytes(utilise),
        "quota_gb": pme.quota_stockage,
        "pourcentage_utilise": pourcentage(utilise, quota_octets),
        "reste_label": format_bytes(max(0, quota_octets - utilise)),
        "nb_fichiers": _fichiers_actifs_qs().filter(proprietaire__profile__pme=pme).count(),
    }
