import re
import unicodedata

from django.db import migrations, models


def _normalize_pme_nom(nom: str) -> str:
    if not nom:
        return ""
    text = unicodedata.normalize("NFKD", nom.strip())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[\s_\-]+", " ", text.lower())
    return text.strip()


def dedupe_pmes(apps, schema_editor):
    PME = apps.get_model("survey", "PME")
    UserProfile = apps.get_model("survey", "UserProfile")
    Reponse = apps.get_model("survey", "Reponse")

    groups = {}
    for pme in PME.objects.all().order_by("date_creation", "pk"):
        norm = _normalize_pme_nom(pme.nom)
        groups.setdefault(norm, []).append(pme)

    for norm, pmes in groups.items():
        keeper = pmes[0]
        keeper.nom_normalise = norm
        keeper.save(update_fields=["nom_normalise"])

        for dup in pmes[1:]:
            UserProfile.objects.filter(pme=dup).update(pme=keeper)
            for reponse in Reponse.objects.filter(pme=dup):
                if Reponse.objects.filter(pme=keeper, question=reponse.question).exists():
                    reponse.delete()
                else:
                    reponse.pme = keeper
                    reponse.save(update_fields=["pme"])
            if dup.a_repondu and not keeper.a_repondu:
                keeper.a_repondu = True
                keeper.save(update_fields=["a_repondu"])
            dup.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("survey", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="pme",
            name="nom_normalise",
            field=models.CharField(
                blank=True,
                editable=False,
                help_text="Nom normalisé pour éviter les doublons (SEVEN AI = SEVEN_AI).",
                max_length=200,
                null=True,
            ),
        ),
        migrations.RunPython(dedupe_pmes, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="pme",
            name="nom_normalise",
            field=models.CharField(
                editable=False,
                help_text="Nom normalisé pour éviter les doublons (SEVEN AI = SEVEN_AI).",
                max_length=200,
                unique=True,
            ),
        ),
    ]
