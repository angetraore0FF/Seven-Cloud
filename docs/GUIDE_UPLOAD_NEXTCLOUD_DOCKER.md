# Guide : upload dynamique avec Docker et Nextcloud

Ce guide décrit comment remplacer le stockage local Django (`MEDIA_ROOT`) par **Nextcloud** orchestré avec **Docker**, pour un upload de fichiers dynamique, scalable et souverain — adapté à Seven Cloud.

---

## 1. Architecture cible

```
┌─────────────┐     HTTP/WebDAV      ┌──────────────┐
│  Seven Cloud │ ◄──────────────────► │  Nextcloud   │
│  (Django)    │     API OCS + DAV    │  (Docker)    │
└─────────────┘                      └──────┬───────┘
       │                                    │
       │ SQLite/Postgres                    │ Volume
       ▼                                    ▼
  Métadonnées                          Fichiers binaires
  (Fichier.nextcloud_path)             (/var/www/html/data)
```

**Principe :** Django ne stocke plus le binaire sur disque local. Il enregistre le chemin Nextcloud dans le modèle `Fichier` et délègue lecture/écriture à un service WebDAV.

---

## 2. Stack Docker (`docker-compose.yml`)

Créez à la racine du projet :

```yaml
services:
  db:
    image: mariadb:11
    restart: unless-stopped
    environment:
      MYSQL_ROOT_PASSWORD: ${MYSQL_ROOT_PASSWORD}
      MYSQL_DATABASE: nextcloud
      MYSQL_USER: nextcloud
      MYSQL_PASSWORD: ${MYSQL_PASSWORD}
    volumes:
      - nextcloud_db:/var/lib/mysql

  nextcloud:
    image: nextcloud:29-apache
    restart: unless-stopped
    ports:
      - "8080:80"
    environment:
      MYSQL_HOST: db
      MYSQL_DATABASE: nextcloud
      MYSQL_USER: nextcloud
      MYSQL_PASSWORD: ${MYSQL_PASSWORD}
      NEXTCLOUD_ADMIN_USER: ${NEXTCLOUD_ADMIN_USER}
      NEXTCLOUD_ADMIN_PASSWORD: ${NEXTCLOUD_ADMIN_PASSWORD}
      NEXTCLOUD_TRUSTED_DOMAINS: localhost 127.0.0.1 django
    volumes:
      - nextcloud_data:/var/www/html
    depends_on:
      - db

  django:
    build: .
    restart: unless-stopped
    ports:
      - "8081:8081"
    environment:
      NEXTCLOUD_URL: http://nextcloud:80
      NEXTCLOUD_ADMIN_USER: ${NEXTCLOUD_ADMIN_USER}
      NEXTCLOUD_ADMIN_PASSWORD: ${NEXTCLOUD_ADMIN_PASSWORD}
      DJANGO_SECRET_KEY: ${DJANGO_SECRET_KEY}
    volumes:
      - .:/app
    depends_on:
      - nextcloud
    command: python manage.py runserver 0.0.0.0:8081

volumes:
  nextcloud_db:
  nextcloud_data:
```

Fichier `.env` (ne pas committer) :

```env
MYSQL_ROOT_PASSWORD=change_me_root
MYSQL_PASSWORD=change_me_nc
NEXTCLOUD_ADMIN_USER=admin
NEXTCLOUD_ADMIN_PASSWORD=change_me_admin
DJANGO_SECRET_KEY=change_me_django
```

Démarrage :

```bash
docker compose up -d
```

Accès Nextcloud : http://localhost:8080  
Accès Seven Cloud : http://localhost:8081

---

## 3. Préparation Nextcloud

### 3.1 Créer un compte applicatif

1. Connectez-vous en admin sur Nextcloud.
2. **Paramètres → Sécurité → App passwords** (ou mot de passe d’application).
3. Générez un token pour `seven-cloud-django`.

### 3.2 Structure des dossiers par PME

Convention recommandée (alignée avec `UserProfile.pme`) :

```
/SevenCloud/
  ├── PME-Acme/
  │   ├── user_jdupont/
  │   └── user_mmartin/
  └── PME-Beta/
      └── user_...
```

Création automatique au premier upload via WebDAV `MKCOL`.

### 3.3 Activer WebDAV

WebDAV est activé par défaut. URL de base :

```
http://nextcloud:80/remote.php/dav/files/<USERNAME>/
```

---

## 4. Modèle Django — extension `Fichier`

Ajoutez dans `survey/models.py` :

```python
class Fichier(models.Model):
    # ... champs existants ...
    nextcloud_path = models.CharField(max_length=512, blank=True)
    nextcloud_etag = models.CharField(max_length=128, blank=True)
    stockage_externe = models.BooleanField(default=False)
```

Migration :

```bash
python manage.py makemigrations survey
python manage.py migrate
```

---

## 5. Service Nextcloud (`survey/services/nextcloud.py`)

Installez la dépendance :

```bash
pip install requests
```

Exemple de service :

```python
import requests
from requests.auth import HTTPBasicAuth
from django.conf import settings


class NextcloudStorage:
    def __init__(self):
        self.base = settings.NEXTCLOUD_URL.rstrip("/")
        self.user = settings.NEXTCLOUD_USER
        self.password = settings.NEXTCLOUD_PASSWORD
        self.dav_root = f"{self.base}/remote.php/dav/files/{self.user}"

    def _auth(self):
        return HTTPBasicAuth(self.user, self.password)

    def upload(self, remote_path: str, file_obj) -> dict:
        url = f"{self.dav_root}/{remote_path.lstrip('/')}"
        resp = requests.put(
            url,
            data=file_obj.read(),
            auth=self._auth(),
            headers={"Content-Type": "application/octet-stream"},
            timeout=120,
        )
        resp.raise_for_status()
        return {"path": remote_path, "etag": resp.headers.get("ETag", "")}

    def delete(self, remote_path: str) -> None:
        url = f"{self.dav_root}/{remote_path.lstrip('/')}"
        requests.delete(url, auth=self._auth(), timeout=30).raise_for_status()

    def download_url(self, remote_path: str) -> str:
        return f"{self.dav_root}/{remote_path.lstrip('/')}"
```

---

## 6. Configuration Django (`core/settings.py`)

```python
NEXTCLOUD_URL = os.environ.get("NEXTCLOUD_URL", "http://localhost:8080")
NEXTCLOUD_USER = os.environ.get("NEXTCLOUD_ADMIN_USER", "admin")
NEXTCLOUD_PASSWORD = os.environ.get("NEXTCLOUD_ADMIN_PASSWORD", "")
NEXTCLOUD_ENABLED = os.environ.get("NEXTCLOUD_ENABLED", "false").lower() == "true"
```

---

## 7. Upload dynamique dans la vue `user_fichiers`

Remplacez la création locale par :

```python
from django.conf import settings
from .services.nextcloud import NextcloudStorage

def _upload_fichier(user, uploaded_file, profile):
    pme_slug = profile.pme.nom.replace(" ", "_") if profile.pme else "sans_pme"
    remote_path = f"SevenCloud/{pme_slug}/{user.username}/{uploaded_file.name}"

    if settings.NEXTCLOUD_ENABLED:
        nc = NextcloudStorage()
        meta = nc.upload(remote_path, uploaded_file)
        return Fichier.objects.create(
            nom=uploaded_file.name,
            proprietaire=user,
            taille=uploaded_file.size,
            chiffre=True,
            nextcloud_path=meta["path"],
            nextcloud_etag=meta.get("etag", ""),
            stockage_externe=True,
        )

    # Fallback local (comportement actuel)
    return Fichier.objects.create(
        nom=uploaded_file.name,
        fichier=uploaded_file,
        proprietaire=user,
        taille=uploaded_file.size,
        chiffre=True,
    )
```

---

## 8. Interface utilisateur — upload dynamique (progression)

### 8.1 Endpoint API JSON (recommandé)

Ajoutez une vue :

```python
@login_required
def api_upload_fichier(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST requis"}, status=405)
    f = request.FILES.get("file")
    if not f:
        return JsonResponse({"error": "Aucun fichier"}, status=400)
    fichier = _upload_fichier(request.user, f, _ensure_user_profile(request.user))
    return JsonResponse({
        "id": fichier.pk,
        "nom": fichier.nom,
        "taille": fichier.taille_formatee(),
        "nextcloud": fichier.stockage_externe,
    })
```

URL : `path("api/fichiers/upload/", api_upload_fichier, name="api_upload_fichier")`

### 8.2 JavaScript (barre de progression)

Dans `interfaces/user/fichiers.html` :

```html
<form id="upload-form" enctype="multipart/form-data">
  {% csrf_token %}
  <input type="file" id="file-input" name="file" required>
  <div id="progress-wrap" class="hidden mt-2">
    <div class="h-2 bg-slate-100 rounded-full overflow-hidden">
      <div id="progress-bar" class="h-full bg-blue-600 transition-all" style="width:0%"></div>
    </div>
    <p id="progress-text" class="text-xs text-slate-500 mt-1"></p>
  </div>
  <button type="submit">Envoyer</button>
</form>

<script>
document.getElementById('upload-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const file = document.getElementById('file-input').files[0];
  if (!file) return;
  const formData = new FormData();
  formData.append('file', file);
  formData.append('csrfmiddlewaretoken', '{{ csrf_token }}');
  const xhr = new XMLHttpRequest();
  xhr.open('POST', '{% url "api_upload_fichier" %}');
  document.getElementById('progress-wrap').classList.remove('hidden');
  xhr.upload.onprogress = (ev) => {
    if (ev.lengthComputable) {
      const pct = Math.round((ev.loaded / ev.total) * 100);
      document.getElementById('progress-bar').style.width = pct + '%';
      document.getElementById('progress-text').textContent = pct + '%';
    }
  };
  xhr.onload = () => {
    if (xhr.status === 200) window.location.reload();
    else alert('Erreur upload');
  };
  xhr.send(formData);
});
</script>
```

---

## 9. Synchronisation des quotas

Nextcloud gère les quotas par utilisateur. Alignez `UserProfile.quota_stockage` :

1. Créez un utilisateur Nextcloud par compte Seven Cloud (script admin OCS).
2. API : `PUT /ocs/v1.php/cloud/users/{uid}` avec `quota=50GB`.

Script périodique (Celery ou cron Docker) :

```python
# management/commands/sync_nextcloud_quotas.py
for profile in UserProfile.objects.select_related('user'):
    # Appel OCS API pour définir le quota
    pass
```

---

## 10. Sécurité

| Mesure | Détail |
|--------|--------|
| Réseau Docker | Django ↔ Nextcloud sur réseau interne `docker compose` |
| HTTPS | Reverse proxy Traefik/Caddy en production |
| Tokens | Mot de passe applicatif, pas le mot de passe admin |
| Isolation PME | Un dossier racine par PME, ACL Nextcloud si besoin |
| Chiffrement | Server-side encryption Nextcloud + mention AES-256 côté Seven Cloud |

---

## 11. Plan de migration depuis le stockage local

1. Déployer Nextcloud + activer `NEXTCLOUD_ENABLED=false` (tests).
2. Migrer les fichiers existants :

```python
# management/commands/migrate_to_nextcloud.py
for f in Fichier.objects.filter(stockage_externe=False).exclude(fichier=''):
    with f.fichier.open('rb') as src:
        nc.upload(path, src)
    f.nextcloud_path = path
    f.stockage_externe = True
    f.save()
```

3. Passer `NEXTCLOUD_ENABLED=true` en production.
4. Conserver `MEDIA_ROOT` pour les photos de profil (ou les migrer aussi).

---

## 12. Vérification

```bash
# Nextcloud accessible
curl -u admin:password -I http://localhost:8080/status.php

# WebDAV PUT test
curl -u admin:password -T test.txt \
  "http://localhost:8080/remote.php/dav/files/admin/SevenCloud/test.txt"

# Django
docker compose exec django python manage.py check
```

---

## 13. Résumé des fichiers à créer/modifier

| Fichier | Action |
|---------|--------|
| `docker-compose.yml` | Créer |
| `.env` | Créer (secrets) |
| `Dockerfile` | Créer pour Django |
| `survey/services/nextcloud.py` | Créer |
| `survey/models.py` | Ajouter `nextcloud_path`, etc. |
| `survey/views.py` | `_upload_fichier` + API |
| `core/settings.py` | Variables Nextcloud |
| `core/urls.py` | Route API upload |
| `interfaces/user/fichiers.html` | JS XMLHttpRequest |

---

## 14. Ressources

- [Nextcloud WebDAV](https://docs.nextcloud.com/server/latest/developer_manual/client_apis/WebDAV/index.html)
- [Nextcloud Docker Hub](https://hub.docker.com/_/nextcloud)
- [OCS API](https://docs.nextcloud.com/server/latest/developer_manual/client_apis/OCS/index.html)

---

*Document généré pour le projet Seven Cloud — upload dynamique via Docker + Nextcloud.*
