import re
from urllib.parse import quote

import requests
from django.conf import settings
from requests.auth import HTTPBasicAuth


class NextcloudError(Exception):
    """Erreur de communication avec Nextcloud."""


class NextcloudStorage:
    def __init__(self):
        self.base = settings.NEXTCLOUD_URL.rstrip("/")
        self.user = settings.NEXTCLOUD_USER
        self.password = settings.NEXTCLOUD_PASSWORD
        self.dav_root = f"{self.base}/remote.php/dav/files/{quote(self.user)}"

    def _auth(self):
        return HTTPBasicAuth(self.user, self.password)

    def _dav_url(self, remote_path: str) -> str:
        parts = [quote(p) for p in remote_path.strip("/").split("/") if p]
        return f"{self.dav_root}/{'/'.join(parts)}"

    def _ensure_parent_dirs(self, remote_path: str) -> None:
        """Crée les dossiers parents via MKCOL si nécessaire."""
        parts = remote_path.strip("/").split("/")[:-1]
        current = ""
        for part in parts:
            current = f"{current}/{part}" if current else part
            url = self._dav_url(current)
            resp = requests.request("MKCOL", url, auth=self._auth(), timeout=30)
            if resp.status_code in (201, 200, 405, 409):
                continue
            if resp.status_code == 400 and "Trusted domain error" in resp.text:
                raise NextcloudError(
                    "Nextcloud refuse la connexion depuis Django : ajoutez « nextcloud » "
                    "aux domaines de confiance (NEXTCLOUD_TRUSTED_DOMAINS)."
                )
            resp.raise_for_status()

    def upload(self, remote_path: str, file_obj) -> dict:
        self._ensure_parent_dirs(remote_path)
        url = self._dav_url(remote_path)
        if hasattr(file_obj, "seek"):
            file_obj.seek(0)
        data = file_obj.read()
        resp = requests.put(
            url,
            data=data,
            auth=self._auth(),
            headers={"Content-Type": "application/octet-stream"},
            timeout=300,
        )
        if not resp.ok:
            detail = resp.text[:200]
            if resp.status_code == 400 and "Trusted domain error" in resp.text:
                raise NextcloudError(
                    "Nextcloud refuse la connexion depuis Django : ajoutez « nextcloud » "
                    "aux domaines de confiance (NEXTCLOUD_TRUSTED_DOMAINS)."
                )
            raise NextcloudError(f"Upload échoué ({resp.status_code}): {detail}")
        return {"path": remote_path, "etag": resp.headers.get("ETag", "").strip('"')}

    def download(self, remote_path: str) -> bytes:
        url = self._dav_url(remote_path)
        resp = requests.get(url, auth=self._auth(), timeout=120)
        if not resp.ok:
            raise NextcloudError(f"Téléchargement échoué ({resp.status_code})")
        return resp.content

    def delete(self, remote_path: str) -> None:
        if not remote_path:
            return
        url = self._dav_url(remote_path)
        resp = requests.delete(url, auth=self._auth(), timeout=30)
        if resp.status_code not in (204, 404):
            raise NextcloudError(f"Suppression échouée ({resp.status_code})")

    def ping(self) -> bool:
        try:
            resp = requests.get(
                f"{self.base}/status.php",
                auth=self._auth(),
                timeout=10,
            )
            return resp.ok
        except requests.RequestException:
            return False


def slug_pme(nom: str) -> str:
    slug = re.sub(r"[^\w\-]", "_", nom.replace(" ", "_"))
    return slug[:80] or "sans_pme"


def build_remote_path(user, profile, filename: str) -> str:
    pme_slug = slug_pme(profile.pme.nom) if profile.pme else "sans_pme"
    safe_name = re.sub(r"[^\w.\-]", "_", filename)
    return f"SevenCloud/{pme_slug}/{user.username}/{safe_name}"
