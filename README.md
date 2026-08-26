# calisthenics-tracker
Suivi calisthenics personnel

## Pipelines de synchronisation

- `sync_to_supabase.py` + `sync_fit.py` (via `run_sync.sh`) : pipeline actuel en production, Gadgetbridge → Drive → rclone/Termux → Supabase.
- `sync_zepp_cloud.py` (branche `feat/zepp-cloud-sync`, pas encore en prod) : alternative qui lit directement le cloud Zepp (login email/mot de passe, API non officielle), sans dépendre du téléphone de Sylvain. Décodage validé pour le sommeil, le PAI, la FC repos et la fréquence respiratoire ; le stress et la VFC ne sont pas encore décodés depuis cette source — voir le docstring en tête du fichier pour le détail.
