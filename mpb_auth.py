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


def get_access_token(supabase_url, supabase_key):
    email = os.environ.get("MPB_EMAIL")
    password = os.environ.get("MPB_PASSWORD")
    if not email or not password:
        sys.exit(
            "MPB_EMAIL et MPB_PASSWORD doivent être définies (variables d'environnement) "
            "avec les identifiants du compte créé dans l'appli. "
            "Ex.: export MPB_EMAIL=... MPB_PASSWORD=..."
        )
    r = requests.post(
        f"{supabase_url}/auth/v1/token?grant_type=password",
        headers={"apikey": supabase_key, "Content-Type": "application/json"},
        json={"email": email, "password": password},
    )
    if r.status_code >= 300:
        sys.exit(f"Échec de connexion Supabase Auth ({r.status_code}): {r.text}")
    return r.json()["access_token"]
