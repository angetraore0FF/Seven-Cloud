# Guide de démarrage — Seven Cloud

Plateforme cloud souveraine pour PME : gestion des entreprises (PME), des utilisateurs, du stockage de fichiers (local ou Nextcloud), des partages et des quotas.

---

## Table des matières

1. [Vue d'ensemble](#1-vue-densemble)
2. [Architecture](#2-architecture)
3. [Prérequis](#3-prérequis)
4. [Structure du projet](#4-structure-du-projet)
5. [Installation (développement local — recommandé)](#5-installation-développement-local--recommandé)
6. [Configuration `.env`](#6-configuration-env)
7. [Nextcloud + Docker](#7-nextcloud--docker)
8. [Lancer l'application](#8-lancer-lapplication)
9. [Premiers comptes et rôles](#9-premiers-comptes-et-rôles)
10. [URLs et parcours utilisateur](#10-urls-et-parcours-utilisateur)
11. [Fonctionnalités principales](#11-fonctionnalités-principales)
12. [Stockage, quotas et Nextcloud](#12-stockage-quotas-et-nextcloud)
13. [Commandes utiles](#13-commandes-utiles)
14. [Dépannage](#14-dépannage)
15. [Production (rappels)](#15-production-rappels)

---

## 1. Vue d'ensemble

| Composant | Technologie | Rôle |
|-----------|-------------|------|
| Application web | **Django 6** | Interface admin + utilisateur, logique métier, quotas |
| Base Django | **SQLite** (dev) ou **MySQL** (optionnel) | Utilisateurs, PME, métadonnées fichiers |
| Stockage fichiers | **Nextcloud** (WebDAV) ou **dossier `media/`** | Binaires des fichiers |
| Base Nextcloud | **MariaDB** (Docker) | Données Nextcloud uniquement |
| Front | **Tailwind CSS** (CDN) + templates Django | UI dans `interfaces/` |
| Conteneurs | **Docker Compose** | MariaDB + Nextcloud (+ Django optionnel) |

**Principe clé** : les **jauges de quota** et les **limites d'upload** sont gérées par **Seven Cloud** (somme des `Fichier.taille` en base). Nextcloud stocke les fichiers dans une arborescence par PME ; il ne remplace pas la logique de quota de l'interface.

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Navigateur                                                  │
│  http://127.0.0.1:8081  (Django runserver)                  │
└───────────────────────────┬─────────────────────────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
  ┌───────────┐      ┌─────────────┐     ┌──────────────┐
  │ SQLite /  │      │  media/     │     │  Nextcloud   │
  │ MySQL     │      │  (local)    │     │  :8085       │
  │ db.sqlite3│      │  si NC off  │     │  WebDAV      │
  └───────────┘      └─────────────┘     └──────┬───────┘
                                                 │
                                          ┌──────▼───────┐
                                          │ MariaDB :3306│
                                          │ (Docker)     │
                                          └──────────────┘
```

**Arborescence Nextcloud** (compte technique Django) :

```
SevenCloud/
  └── <nom_pme_slug>/
        └── <username>/
              └── fichier.pdf
```

---

## 3. Prérequis

### Développement local (recommandé)

| Outil | Version minimale |
|-------|------------------|
| Python | 3.11 ou 3.12 |
| pip | récent |
| Git | optionnel |

### Avec stockage Nextcloud

| Outil | Usage |
|-------|--------|
| **Docker Desktop** | MariaDB + Nextcloud |
| Ports libres | **8085** (Nextcloud), **3306** (MariaDB si exposé), **8081** (Django) |

### Optionnel

- `mysqlclient` — uniquement si `DJANGO_USE_MYSQL=true`
- PowerShell ou terminal Windows

---

## 4. Structure du projet

```
Seven Cloud/
├── core/                    # Projet Django (settings, urls)
│   ├── settings.py          # Config + chargement .env
│   └── urls.py              # Routes principales
├── survey/                  # Application métier
│   ├── models.py            # PME, UserProfile, Fichier, demandes…
│   ├── views.py             # Vues admin + utilisateur
│   ├── forms.py
│   ├── middleware.py        # Anti-cache pages authentifiées
│   ├── services/
│   │   ├── nextcloud.py     # WebDAV upload/download/delete
│   │   ├── fichiers.py      # Upload unifié local / Nextcloud
│   │   └── quota.py         # Vérification et stats quotas
│   ├── utils/storage.py     # Formatage Ko/Mo/Go
│   └── management/commands/
│       ├── check_nextcloud.py
│       └── migrate_to_nextcloud.py
├── interfaces/              # Templates HTML
│   ├── admin/               # Espace administrateur
│   ├── user/                # Espace utilisateur
│   └── registration/        # Login / inscription
├── media/                   # Fichiers locaux (si Nextcloud désactivé)
├── docs/
│   ├── GUIDE_DEMARRAGE.md   # Ce fichier
│   └── GUIDE_UPLOAD_NEXTCLOUD_DOCKER.md
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── manage.py
├── .env.example
└── .env                     # À créer (non versionné)
```

---

## 5. Installation (développement local — recommandé)

### Étape 1 — Cloner / ouvrir le projet

```powershell
cd "C:\Users\Ange\Seven Cloud"
```

### Étape 2 — Environnement virtuel Python

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

Si l'exécution de scripts est bloquée :

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

### Étape 3 — Dépendances Python

```powershell
pip install -r requirements.txt
```

Contenu de `requirements.txt` :

- Django 6.x
- Pillow (photos PME / profils)
- requests (WebDAV Nextcloud)
- python-dotenv (lecture du fichier `.env`)

### Étape 4 — Fichier d'environnement

```powershell
copy .env.example .env
```

Éditez `.env` (voir [section 6](#6-configuration-env)).

**Important pour démarrer sans erreur MySQL** :

```env
DJANGO_USE_MYSQL=false
```

→ Django utilisera `db.sqlite3` à la racine du projet.

### Étape 5 — Base de données Django

```powershell
python manage.py migrate
```

### Étape 6 — (Optionnel) Superutilisateur Django

Pour accéder à `/admin/` (interface Django native) :

```powershell
python manage.py createsuperuser
```

Pour l'**interface Seven Cloud admin** (`/admin/dashboard/`), voir [section 9](#9-premiers-comptes-et-rôles).

---

## 6. Configuration `.env`

| Variable | Description | Exemple dev |
|----------|-------------|-------------|
| `DJANGO_USE_MYSQL` | `false` = SQLite (recommandé en dev) | `false` |
| `DJANGO_SECRET_KEY` | Clé secrète Django | chaîne longue aléatoire |
| `ALLOWED_HOSTS` | Hôtes autorisés | `localhost,127.0.0.1` |
| `DJANGO_DEBUG` | Mode debug (non défini = `true`) | `true` |
| `NEXTCLOUD_ENABLED` | Upload vers Nextcloud | `true` ou `false` |
| `NEXTCLOUD_URL` | URL vue **depuis votre PC** | `http://localhost:8085` |
| `NEXTCLOUD_USER` | Compte WebDAV | `admin_ange` |
| `NEXTCLOUD_PASSWORD` | Mot de passe ou **mot de passe d'application** | `xxxx-xxxx-…` |
| `NEXTCLOUD_ADMIN_USER` | Admin Nextcloud (install Docker) | `admin_ange` |
| `NEXTCLOUD_ADMIN_PASSWORD` | Mot de passe admin NC | `…` |
| `MYSQL_ROOT_PASSWORD` | Root MariaDB (Docker) | `change_me_root` |
| `MYSQL_PASSWORD` | Mot de passe BDD Nextcloud | `change_me_nc` |

**Dans Docker Compose**, Django reçoit `NEXTCLOUD_URL=http://nextcloud:80` (réseau interne). En local avec `runserver`, gardez `http://localhost:8085`.

---

## 7. Nextcloud + Docker

### Démarrer les conteneurs

```powershell
cd "C:\Users\Ange\Seven Cloud"
docker compose up -d
```

Services :

| Service | Port hôte | Rôle |
|---------|-----------|------|
| `db` | 3306 | MariaDB pour Nextcloud |
| `nextcloud` | **8085** | Interface + WebDAV |
| `django` | 8081 | Django en conteneur (optionnel) |

Vérifier :

```powershell
docker compose ps
```

### Premier accès Nextcloud

1. Ouvrir **http://localhost:8085**
2. Créer le compte admin (identifiants = `NEXTCLOUD_ADMIN_*` dans `.env`)
3. **Paramètres → Sécurité → Mots de passe d'application**
4. Créer un mot de passe d'application pour le compte utilisé par Django
5. Copier ce mot de passe dans `.env` → `NEXTCLOUD_PASSWORD=…`

### Tester la connexion depuis Django

```powershell
python manage.py check_nextcloud
```

Résultat attendu : `Nextcloud accessible (status.php OK).`

### Activer l'upload Nextcloud

Dans `.env` :

```env
NEXTCLOUD_ENABLED=true
NEXTCLOUD_URL=http://localhost:8085
```

Redémarrer `runserver` après modification du `.env`.

---

## 8. Lancer l'application

### Mode recommandé : Django en local, Nextcloud en Docker

```powershell
.\venv\Scripts\Activate.ps1
python manage.py runserver 8081
```

Ouvrir : **http://127.0.0.1:8081**

### Mode tout-en-Docker

```powershell
docker compose up -d
```

Django : **http://localhost:8081** (conteneur `django`).

> En mode Docker Django, le code est monté en volume ; les migrations s'exécutent au démarrage du conteneur.

---

## 9. Premiers comptes et rôles

### Rôles

| Rôle | `UserProfile.role` | Accès |
|------|-------------------|--------|
| **Administrateur plateforme** | `admin` | `/admin/dashboard/`, PME, utilisateurs, sécurité |
| **Employé** | `employe` | `/dashboard/`, fichiers, partages |
| RH, Compta, Invité | `rh`, `compta`, `invite` | Même espace utilisateur |

Un utilisateur est admin si :

- `user.is_superuser`, **ou**
- `user.profile.role == 'admin'`

### Créer un administrateur Seven Cloud

**Option A — Inscription puis promotion** (shell Django) :

```powershell
python manage.py shell
```

```python
from django.contrib.auth.models import User
from survey.models import UserProfile
u = User.objects.get(username="votre_user")
p, _ = UserProfile.objects.get_or_create(user=u)
p.role = "admin"
p.save()
```

**Option B — Superuser + profil admin** :

```powershell
python manage.py createsuperuser
```

Puis en shell : `UserProfile.objects.filter(user=u).update(role='admin')`.

### Inscription utilisateur (PME)

**http://127.0.0.1:8081/register/**

- Crée un compte Django + `UserProfile` (rôle `employe`, **50 Go** par défaut)
- Si un nom d'entreprise est saisi → création d'une **PME** (quota entreprise **200 Go** par défaut)

---

## 10. URLs et parcours utilisateur

### Public

| URL | Nom | Description |
|-----|-----|-------------|
| `/` | `router_dashboard` | Redirige vers admin ou user |
| `/login/` | `login` | Connexion |
| `/register/` | `register` | Inscription |
| `/logout/` | `logout` | Déconnexion (POST) |

### Espace utilisateur

| URL | Description |
|-----|-------------|
| `/dashboard/` | Tableau de bord, jauges stockage |
| `/dashboard/fichiers/` | Upload, liste, favoris, corbeille |
| `/dashboard/partages/` | Fichiers reçus / envoyés |
| `/dashboard/parametres/` | Compte, photo, demande quota / MDP |
| `/api/fichiers/upload/` | Upload AJAX (POST) |
| `/fichiers/<id>/telecharger/` | Téléchargement sécurisé |

### Espace administrateur

| URL | Description |
|-----|-------------|
| `/admin/dashboard/` | Vue d'ensemble |
| `/admin/pme/` | CRUD PME + quota entreprise |
| `/admin/utilisateurs/` | Utilisateurs, demandes MDP / quota |
| `/admin/securite/` | Normes de sécurité affichées |
| `/admin/parametres/` | Compte admin |
| `/admin/` | Admin Django natif (superuser) |

---

## 11. Fonctionnalités principales

### Utilisateur

- Upload de fichiers (XHR + barre de progression si Nextcloud actif)
- Vues : Tous / Favoris / Corbeille
- Partage avec un autre utilisateur (souvent collègue de la même PME)
- Téléchargement via proxy Django (local ou Nextcloud)
- Demande de changement de mot de passe (traitée par l'admin)
- Demande d'augmentation de quota personnel
- Jauges : espace **personnel** + espace **PME** (si rattaché)

### Administrateur

- Gestion des PME (logo, quota stockage entreprise, sondage)
- Gestion des utilisateurs (PME, rôle, quota, photo)
- Traitement des demandes MDP et quota
- Tableau des normes de sécurité (informatif)

### Sécurité session

- Pages authentifiées : en-têtes **no-cache** (`NoCacheAuthenticatedMiddleware`)
- Déconnexion : `session.flush()` + POST uniquement
- Vues protégées : `@never_cache`, `@login_required`

---

## 12. Stockage, quotas et Nextcloud

### Deux niveaux de quota

| Niveau | Modèle | Défaut | Où modifier |
|--------|--------|--------|-------------|
| **Utilisateur** | `UserProfile.quota_stockage` | 50 Go | Admin → Utilisateurs |
| **PME** | `PME.quota_stockage` | 200 Go | Admin → PME |

À chaque upload, les **deux** plafonds sont vérifiés. L'occupation affichée = somme des `Fichier.taille` (**hors corbeille**).

### Nextcloud activé vs désactivé

| `NEXTCLOUD_ENABLED` | Comportement |
|---------------------|--------------|
| `false` | Fichiers dans `media/fichiers/…` |
| `true` | WebDAV vers Nextcloud ; métadonnées en base |

### Migrer d'anciens fichiers locaux vers Nextcloud

```powershell
python manage.py migrate_to_nextcloud --dry-run
python manage.py migrate_to_nextcloud
```

---

## 13. Commandes utiles

```powershell
# Vérifier le projet
python manage.py check

# Migrations
python manage.py makemigrations
python manage.py migrate

# Nextcloud
python manage.py check_nextcloud

# Serveur dev
python manage.py runserver 8081

# Docker
docker compose up -d
docker compose down
docker compose logs nextcloud
docker compose logs django
```

---

## 14. Dépannage

### `NEXTCLOUD_PASSWORD est vide`

→ Le fichier `.env` n'est pas chargé ou la variable est absente. Vérifiez que `python-dotenv` est installé et que `.env` existe à la racine.

### `Can't connect to server on '127.0.0.1' (10061)` (MySQL)

→ `DJANGO_USE_MYSQL=true` mais MariaDB n'est pas démarré. **Solution dev** : `DJANGO_USE_MYSQL=false` dans `.env`.

### Erreur YAML `docker compose`

→ Vérifier la syntaxe `CLE: valeur` (pas `CLE=valeur` dans les blocs `environment:`).

### La jauge reste à 0 après upload

→ Normal pour de très petits fichiers si l'affichage était en Go uniquement ; l'interface affiche maintenant **Mo/Ko**. Rechargez la page. Vérifiez que le fichier n'est pas en corbeille.

### Partages « Envoyés » vides

→ Le fichier doit être partagé avec au moins un utilisateur (champ `share_username` sur Mes fichiers). Après partage, vous êtes redirigé vers **Partagés**.

### Upload échoue (502 / erreur Nextcloud)

1. `python manage.py check_nextcloud`
2. Mot de passe d'application Nextcloud à jour dans `.env`
3. Nextcloud démarré : `docker compose ps`
4. Quota utilisateur ou PME non dépassé

### Port 8085 déjà utilisé

→ Modifier le mapping dans `docker-compose.yml` (ex. `"8086:80"`) et `NEXTCLOUD_URL=http://localhost:8086`.

---

## 15. Production (rappels)

- `DJANGO_DEBUG=false`
- `DJANGO_SECRET_KEY` fort et unique
- `ALLOWED_HOSTS` avec votre domaine
- HTTPS (reverse proxy : nginx, Caddy, Traefik)
- Ne pas exposer MariaDB (`3306`) publiquement
- Sauvegardes : `db.sqlite3` ou MySQL + volumes Docker `nextcloud_data`, `nextcloud_db`
- Fichier `.env` **jamais** commité (déjà dans `.gitignore`)

---

## Démarrage rapide (checklist)

```
[ ] Python 3.11+ et venv activé
[ ] pip install -r requirements.txt
[ ] copy .env.example .env  →  DJANGO_USE_MYSQL=false
[ ] python manage.py migrate
[ ] docker compose up -d  (si Nextcloud)
[ ] Configurer mot de passe d'application Nextcloud dans .env
[ ] NEXTCLOUD_ENABLED=true
[ ] python manage.py check_nextcloud
[ ] python manage.py runserver 8081
[ ] Créer / promouvoir un compte admin
[ ] Tester upload sur /dashboard/fichiers/
```

---

*Document généré pour le dépôt Seven Cloud — complément technique : `docs/GUIDE_UPLOAD_NEXTCLOUD_DOCKER.md`.*
