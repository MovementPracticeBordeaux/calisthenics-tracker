"""Authentification Supabase Auth partagée par les scripts de synchronisation.

Depuis le verrouillage des policies RLS de la base, la clé anon seule ne
donne plus accès aux données : chaque script doit se connecter avec le
compte Supabase Auth personnel (le même que celui créé dans l'appli), dont
les identifiants sont lus depuis les variables d'environnement MPB_EMAIL et
MPB_PASSWORD — jamais codés en dur dans le dépôt.
"""
import os
import sys

import requests

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ENV_FILE = os.path.join(_SCRIPT_DIR, ".env")


def _load_env_file():
    """Charge des KEY=VALUE depuis un fichier .env local à côté des scripts
    (non suivi par git, voir .gitignore) — évite d'avoir à ré-exporter les
    identifiants à chaque session Termux. Les variables d'environnement déjà
    définies restent prioritaires (setdefault)."""
    if not os.path.isfile(_ENV_FILE):
        return
    with open(_ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def get_access_token(supabase_url, supabase_key):
    _load_env_file()
    email = os.environ.get("MPB_EMAIL")
    password = os.environ.get("MPB_PASSWORD")
    if not email or not password:
        sys.exit(
            "MPB_EMAIL et MPB_PASSWORD doivent être définies avec les identifiants "
            "du compte créé dans l'appli — soit en variables d'environnement "
            "(export MPB_EMAIL=... MPB_PASSWORD=...), soit dans un fichier .env "
            f"à côté des scripts ({_ENV_FILE})."
        )
    r = requests.post(
        f"{supabase_url}/auth/v1/token?grant_type=password",
        headers={"apikey": supabase_key, "Content-Type": "application/json"},
        json={"email": email, "password": password},
    )
    if r.status_code >= 300:
        sys.exit(f"Échec de connexion Supabase Auth ({r.status_code}): {r.text}")
    return r.json()["access_token"]
