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
- **Scripts (`sync_fit.py`, `sync_to_supabase.py`)** : définis les mêmes
  identifiants dans l'environnement avant de les lancer :

  ```bash
  export MPB_EMAIL="ton-email@exemple.com"
  export MPB_PASSWORD="ton-mot-de-passe"
  python sync_fit.py
  python sync_to_supabase.py /chemin/vers/Gadgetbridge.db
  ```

  Ne jamais committer ces identifiants — utilise des variables
  d'environnement (ou un fichier local `.env` non suivi par git, voir
  `.gitignore`).
