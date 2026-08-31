# calisthenics-tracker
Suivi calisthenics personnel

## Authentification

L'appli et les scripts de synchronisation utilisent un vrai compte Supabase
Auth (email/mot de passe) — la clé anon seule n'a plus accès aux données
(policies RLS restreintes aux requêtes authentifiées).

- **Appli (`index.html`)** : à la première ouverture, clique sur "Première
  visite ? Créer un compte" et choisis un email + un mot de passe. Les
  visites suivantes se reconnectent automatiquement (session gardée en
  local sur l'appareil).
- **Scripts (`sync_fit.py`, `sync_to_supabase.py`)** : ont besoin des mêmes
  identifiants, de l'une des deux façons suivantes.

  Option A — variables d'environnement (à refaire à chaque session) :
  ```bash
  export MPB_EMAIL="ton-email@exemple.com"
  export MPB_PASSWORD="ton-mot-de-passe"
  python sync_fit.py
  python sync_to_supabase.py /chemin/vers/Gadgetbridge.db
  ```

  Option B — fichier `.env` local (une seule fois, pas besoin de ré-exporter) :
  ```bash
  cp .env.example .env
  # puis édite .env avec ton email et ton mot de passe
  ```
  `.env` n'est jamais suivi par git (voir `.gitignore`) — seul `.env.example`
  (sans secret) est versionné, comme gabarit.

  Ne jamais committer tes vrais identifiants.
