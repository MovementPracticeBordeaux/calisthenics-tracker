#!/data/data/com.termux/files/usr/bin/bash
# Télécharge la dernière copie de Gadgetbridge.db depuis Google Drive (via rclone),
# puis lance la synchro vers Supabase.
set -e
HOME_DIR="/data/data/com.termux/files/home"

rclone copy gdrive:Gadgetbridge_Export/Gadgetbridge.db "$HOME_DIR" --update

python "$HOME_DIR/sync_to_supabase.py" "$HOME_DIR/Gadgetbridge.db"
