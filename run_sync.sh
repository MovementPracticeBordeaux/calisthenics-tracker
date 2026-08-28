#!/data/data/com.termux/files/usr/bin/bash
# Télécharge les données depuis Google Drive (via rclone) puis synchronise
# vers Supabase : d'abord les données quotidiennes (Gadgetbridge), ensuite
# les séances .fit — désormais exportées par Gadgetbridge lui-même, avec
# repli sur l'ancien dossier Zepp au cas où il redevienne actif un jour.
set -e
HOME_DIR="/data/data/com.termux/files/home"

# --- Données quotidiennes (FC, VFC, stress, sommeil) ---
rclone copy gdrive:Gadgetbridge_Export/Gadgetbridge.db "$HOME_DIR" --update
python "$HOME_DIR/sync_to_supabase.py" "$HOME_DIR/Gadgetbridge.db"

# --- Séances (.fit) : zones cardiaques Karvonen seconde par seconde,
# rattachées au planning réel des cours ---
mkdir -p "$HOME_DIR/zepp_fit"
rclone copy gdrive:Gadgetbridge_Export "$HOME_DIR/zepp_fit" --include "*.fit" --update
rclone copy gdrive:Zepp "$HOME_DIR/zepp_fit" --include "*.fit" --update
python "$HOME_DIR/sync_fit.py" "$HOME_DIR/zepp_fit"
