#!/usr/bin/env python3
"""Synchronise les séances Zepp (.fit) vers Supabase.

Décode les fichiers .fit exportés automatiquement par Zepp vers Google Drive
et calcule les zones cardiaques par la méthode Karvonen (réserve cardiaque),
à partir des données seconde par seconde — plus juste que le %FCmax utilisé
par défaut par la montre, car elle intègre la FC de repos.
"""
import datetime
import glob
import json
import os
import sys

import fitparse
import requests

SUPABASE_URL = "https://zwltvhjitrvlrhbivdfm.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inp3bHR2aGppdHJ2bHJoYml2ZGZtIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODczMTMxMjcsImV4cCI6MjEwMjg4OTEyN30.hTD6h2r9dKdKaJ15vcFU6eN8WScA1rr_nT2dNByT6co"

# Profil — FC max réellement observée (pas une formule d'âge, qui sous-estimait
# à 179) et FC repos réelle. À réviser si un test d'effort donne mieux.
HR_MAX = 187
HR_REST = 47

FIT_DIR = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/zepp_fit")


def karvonen_thresholds():
    reserve = HR_MAX - HR_REST
    return [round(HR_REST + reserve * p) for p in (0.5, 0.6, 0.7, 0.8, 0.9)]


def parse_fit(path):
    """Extrait une séance d'un fichier .fit, avec zones Karvonen."""
    try:
        ff = fitparse.FitFile(path)
        sessions = [{f.name: f.value for f in m} for m in ff.get_messages("session")]
        if not sessions:
            return None
        s = sessions[0]
        start = s.get("start_time")
        dur_s = s.get("total_elapsed_time")
        if not start or not dur_s:
            return None

        hrs = []
        for r in ff.get_messages("record"):
            d = {f.name: f.value for f in r}
            hr = d.get("heart_rate")
            if hr and 0 < hr < 250:
                hrs.append(hr)

        th = karvonen_thresholds()
        z = [0] * 5
        for hr in hrs:
            if hr < th[0]:
                continue  # sous zone 1 : repos, non compté
            elif hr < th[1]:
                z[0] += 1
            elif hr < th[2]:
                z[1] += 1
            elif hr < th[3]:
                z[2] += 1
            elif hr < th[4]:
                z[3] += 1
            else:
                z[4] += 1

        end = start + datetime.timedelta(seconds=dur_s)
        zones_official = s.get("time_in_hr_zone") or [0] * 6

        return {
            "day": start.strftime("%Y-%m-%d"),
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
            "duration_min": round(dur_s / 60, 1),
            # Zones officielles de la montre (%FCmax) — conservées pour comparaison
            "zone_leger_min": round(zones_official[1] / 60, 1),
            "zone_intensif_min": round(zones_official[2] / 60, 1),
            "zone_aerobique_min": round(zones_official[3] / 60, 1),
            "zone_anaerobie_min": round(zones_official[4] / 60, 1),
            "zone_vo2max_min": round(zones_official[5] / 60, 1),
            # Zones Karvonen recalculées seconde par seconde
            "kz_leger_min": round(z[0] / 60, 1),
            "kz_intensif_min": round(z[1] / 60, 1),
            "kz_aerobique_min": round(z[2] / 60, 1),
            "kz_anaerobie_min": round(z[3] / 60, 1),
            "kz_vo2max_min": round(z[4] / 60, 1),
            "kz_hrmax_used": HR_MAX,
            "kz_hrrest_used": HR_REST,
            "training_effect_aerobic": s.get("total_training_effect"),
            "training_effect_anaerobic": s.get("total_anaerobic_training_effect"),
            "calories": s.get("total_calories"),
            "hr_points": len(hrs),
            "source": "zepp_official",
        }
    except Exception as exc:
        print(f"  ! {os.path.basename(path)} illisible : {exc}")
        return None


def push(rows):
    if not rows:
        print("Aucune séance à synchroniser.")
        return
    resp = requests.post(
        f"{SUPABASE_URL}/rest/v1/workout_sessions?on_conflict=start_time",
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=minimal",
        },
        data=json.dumps(rows),
    )
    if resp.status_code >= 300:
        print(f"Erreur Supabase ({resp.status_code}): {resp.text}")
    else:
        print(f"{len(rows)} séance(s) synchronisée(s) vers Supabase.")


if __name__ == "__main__":
    files = sorted(glob.glob(os.path.join(FIT_DIR, "*.fit")))
    if not files:
        print(f"Aucun fichier .fit dans {FIT_DIR}")
        sys.exit(0)
    rows = []
    for fp in files:
        row = parse_fit(fp)
        if row:
            rows.append(row)
    # Déduplique par start_time — Postgres refuse deux fois la même clé dans
    # un seul envoi.
    by_start = {}
    for r in rows:
        by_start[r["start_time"]] = r
    push(list(by_start.values()))
