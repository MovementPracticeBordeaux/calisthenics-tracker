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
- **Scripts (`sync_fit.py`, `sync_to_supabase.py`, `sync_zepp_cloud.py`)** :
  ont besoin des mêmes identifiants, de l'une des deux façons suivantes.

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

## Pipelines de synchronisation

- `sync_to_supabase.py` (Gadgetbridge) + `sync_fit.py` (.fit), via
  `run_sync.sh` : pipeline principal en production. Gadgetbridge (appairé en
  Bluetooth avec le bracelet, aucune dépendance au cloud Zepp) exporte vers
  Google Drive, `rclone` rapatrie, ces deux scripts poussent vers Supabase.
  Couvre tout : sommeil, VFC, stress, FC repos, PAI, fréquence respiratoire,
  et les séances (.fit) avec zones cardiaques Karvonen seconde par seconde.

- `sync_zepp_cloud.py` (optionnel, **en plus** de Gadgetbridge, pas à sa
  place) : lit directement le cloud Zepp (login email/mot de passe, API non
  officielle reverse-engineeré) pour les mêmes données quotidiennes que
  `sync_to_supabase.py`, sans dépendre du téléphone/Google Drive. Décodage
  validé champ par champ pour le sommeil, les pas, la FC repos, le PAI, la
  fréquence respiratoire et la VFC.

  Requiert `ZEPP_EMAIL`/`ZEPP_PASSWORD` (voir `.env.example`) en plus des
  identifiants Supabase. Le jeton de session est mis en cache localement
  (`.zepp_token_cache.json`, jamais commité) et réutilisé d'un lancement à
  l'autre — le mot de passe n'est renvoyé à Zepp que si ce cache est absent,
  expiré, ou rejeté, jamais à chaque exécution (se reconnecter à chaque fois
  ressemblerait à un comportement de bot pour les systèmes anti-abus de
  Zepp, avec un vrai risque de blocage du compte).

  **Limitation connue** : le stress (`stress_avg`/`stress_max`) n'est pas
  décodable avec un effort raisonnable (protobuf interne sans schéma public,
  aucune corrélation trouvée après test exhaustif — voir le docstring en
  tête du fichier) — ce script n'écrit donc jamais ces colonnes, qui restent
  alimentées par Gadgetbridge.

  Comme pour tout accès à une API privée non documentée : ça peut casser
  sans préavis si Zepp change son protocole. `run_sync.sh` tolère un échec
  de cette étape sans jamais interrompre le pipeline principal.
