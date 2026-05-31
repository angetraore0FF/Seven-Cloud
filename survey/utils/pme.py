import re
import unicodedata

from ..models import PME


def normalize_pme_nom(nom: str) -> str:
    """Clé de comparaison insensible à la casse, espaces et tirets bas."""
    if not nom:
        return ""
    text = unicodedata.normalize("NFKD", nom.strip())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[\s_\-]+", " ", text.lower())
    return text.strip()


def get_or_create_pme(
    nom: str,
    *,
    secteur: str = "autre",
    email_contact: str = "",
    telephone: str = "",
    adresse: str = "",
) -> PME | None:
    """Retourne la PME existante (même nom normalisé) ou en crée une nouvelle."""
    nom_clean = nom.strip()
    if not nom_clean:
        return None

    norm = normalize_pme_nom(nom_clean)
    existing = PME.objects.filter(nom_normalise=norm).first()
    if existing:
        return existing

    return PME.objects.create(
        nom=nom_clean,
        nom_normalise=norm,
        secteur=secteur or "autre",
        email_contact=email_contact,
        telephone=telephone or "",
        adresse=adresse or "",
    )
