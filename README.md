# calisthenics-tracker
Suivi calisthenics personnel

## Pipelines de synchronisation

- `sync_to_supabase.py` + `sync_fit.py` (via `run_sync.sh`) : pipeline actuel en production, Gadgetbridge → Drive → rclone/Termux → Supabase.
- `sync_zepp_cloud.py` (branche `feat/zepp-cloud-sync`, pas encore en prod) : alternative qui lit directement le cloud Zepp (login email/mot de passe, API non officielle), sans dépendre du téléphone de Sylvain. Décodage validé pour le sommeil, le PAI, la FC repos, la fréquence respiratoire et la VFC. Le stress reste sur Gadgetbridge : le blob cloud correspondant s'est avéré être l'état interne brut de l'algorithme (pas le score affiché), non décodable avec un effort raisonnable — voir le docstring en tête du fichier pour le détail.
