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

## Fréquence de synchronisation

Depuis que `sync_to_supabase.py` conserve aussi les échantillons minute par
minute (`wearable_minute_samples`, rétention 3 jours — FC, pas, VFC, stress
horodatés), l'intérêt des courbes intraday dépend directement de la
fraîcheur de la donnée : elles ne peuvent montrer que ce qui a déjà été
synchronisé. Pour que ça reste utile en cours de journée (pas seulement le
lendemain), les deux maillons de la chaîne doivent tourner **toutes les
heures**, l'un après l'autre :

1. **Gadgetbridge (sur le téléphone)** — Réglages → Gestion des données →
   Export automatique de la base : mets l'intervalle le plus court proposé
   (1h si disponible). C'est ce qui écrit `Gadgetbridge.db` (et les `.fit`)
   dans le dossier exporté vers Google Drive ; sans ça, `run_sync.sh` n'a
   rien de plus récent à lire même s'il tourne plus souvent.

2. **`run_sync.sh` (Termux)** — programmé via `termux-job-scheduler`, pas
   cron (voir le commentaire en tête du script). Pour le passer à toutes
   les heures :
   ```bash
   termux-job-scheduler --cancel --job-id 1000   # si un job existait déjà avec cet id
   termux-job-scheduler --job-id 1000 --period-ms 3600000 --persisted true --script ~/run_sync.sh
   ```
   `--period-ms 3600000` = 1h ; `--persisted true` = survit à un redémarrage
   du téléphone. Android peut légèrement décaler l'exécution (économie de
   batterie), donc ça reste "environ toutes les heures", pas une horloge
   exacte — sans importance ici.

Décale si besoin l'un des deux de quelques minutes (ex. export Gadgetbridge
à :00, `run_sync.sh` à :10) pour laisser le temps à l'export + la synchro
Drive de se terminer avant que `run_sync.sh` aille lire le fichier.
