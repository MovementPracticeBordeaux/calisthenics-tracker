#!/data/data/com.termux/files/usr/bin/bash
# Télécharge les données depuis Google Drive (via rclone) puis synchronise
# vers Supabase : d'abord les données quotidiennes (Gadgetbridge), ensuite
# les séances .fit — désormais exportées par Gadgetbridge lui-même, avec
# repli sur l'ancien dossier Zepp au cas où il redevienne actif un jour.
HOME_DIR="/data/data/com.termux/files/home"

# Auto-journalisation : peu importe comment ce script est lancé (cron,
# termux-job-scheduler, manuellement), tout va dans sync.log. C'est
# termux-job-scheduler qui ne redirige rien tout seul, contrairement à cron.
exec >> "$HOME_DIR/sync.log" 2>&1
echo "--- $(date) ---"

set -e

# --- Données quotidiennes (FC, VFC, stress, sommeil) ---
rclone copy gdrive:Gadgetbridge_Export/Gadgetbridge.db "$HOME_DIR" --update
python "$HOME_DIR/sync_to_supabase.py" "$HOME_DIR/Gadgetbridge.db"

# --- Séances (.fit) : zones cardiaques Karvonen seconde par seconde,
# rattachées au planning réel des cours ---
mkdir -p "$HOME_DIR/zepp_fit"
rclone copy gdrive:Gadgetbridge_Export "$HOME_DIR/zepp_fit" --include "*.fit" --update
rclone copy gdrive:Zepp "$HOME_DIR/zepp_fit" --include "*.fit" --update
python "$HOME_DIR/sync_fit.py" "$HOME_DIR/zepp_fit"

# --- (Optionnel) Données quotidiennes via le cloud Zepp, EN PLUS de
# Gadgetbridge ci-dessus (pas à sa place — voir sync_zepp_cloud.py et le
# README, section stress). API non officielle, plus fragile que le reste du
# pipeline : un échec ici ne doit jamais faire échouer le reste du script.
python "$HOME_DIR/sync_zepp_cloud.py" \
  || echo "! sync_zepp_cloud.py a échoué (ou ZEPP_EMAIL non configuré) — Gadgetbridge reste la source de référence"
