#!/usr/bin/env python3
"""Synchronise les séances Zepp (.fit) vers Supabase.

Décode les fichiers .fit exportés automatiquement vers Google Drive et
calcule les zones cardiaques par la méthode Karvonen (réserve cardiaque), à
partir des données seconde par seconde. Chaque fichier est croisé avec le
planning réel des cours (class_schedule) pour distinguer une vraie séance
d'un trajet ou d'une activité hors planning, plutôt que de deviner à la
seule durée.
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


def sb_get(path):
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/{path}",
        headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
    )
    r.raise_for_status()
    return r.json()


def get_semaine(day, ref_cache):
    """Détermine la semaine A/B pour une date donnée, à partir de la
    référence stockée en base (alternance chaque semaine depuis le lundi de
    référence)."""
    if "ref" not in ref_cache:
        ref = sb_get("schedule_reference?select=date_lundi_reference,semaine_ce_lundi&limit=1")
        ref_cache["ref"] = ref[0] if ref else None
    ref = ref_cache["ref"]
    if not ref:
        return None
    ref_monday = datetime.date.fromisoformat(ref["date_lundi_reference"])
    ref_letter = ref["semaine_ce_lundi"]
    this_monday = day - datetime.timedelta(days=day.weekday())
    weeks_diff = (this_monday - ref_monday).days // 7
    if weeks_diff % 2 == 0:
        return ref_letter
    return "A" if ref_letter == "B" else "B"


def load_schedule():
    """Charge tout le planning une fois, pour éviter un appel réseau par fichier."""
    return sb_get("class_schedule?select=discipline,jour_semaine,semaine,heure_debut,heure_fin&actif=eq.true")


def match_class(start_local, schedule, ref_cache):
    """Cherche un cours du planning dont le créneau (±20 min de tolérance)
    contient l'heure de début du fichier .fit — c'est ce qui permet de
    distinguer une vraie séance de cours (1h) d'un trajet ou d'une activité
    hors planning, sans se fier uniquement à la durée."""
    day = start_local.date()
    semaine = get_semaine(day, ref_cache)
    jour_semaine = start_local.isoweekday()  # 1=lundi ... 7=dimanche
    tol = datetime.timedelta(minutes=20)
    for c in schedule:
        if c["jour_semaine"] != jour_semaine or c["semaine"] != semaine:
            continue
        c_start = datetime.datetime.combine(day, datetime.time.fromisoformat(c["heure_debut"]))
        if c_start - tol <= start_local <= c_start + tol:
            return c["discipline"]
    return None


def match_logged_session(start_local, logged_cache):
    """Repli si aucun cours officiel ne correspond : vérifie si l'horaire du
    fichier .fit correspond à une séance que Sylvain a lui-même loguée ce
    jour-là (via le champ heure du formulaire) — couvre les séances hors
    planning (perso, remplacement, week-end...)."""
    day = start_local.date()
    day_str = day.isoformat()
    if day_str not in logged_cache:
        rows = sb_get(f"sessions?session_date=eq.{day_str}&select=discipline,session_time")
        logged_cache[day_str] = [r for r in rows if r.get("session_time") and r.get("discipline")]
    tol = datetime.timedelta(minutes=30)
    for s in logged_cache[day_str]:
        s_time = datetime.time.fromisoformat(s["session_time"])
        s_start = datetime.datetime.combine(day, s_time)
        if s_start - tol <= start_local <= s_start + tol:
            return s["discipline"]
    return None


def is_bike_trip(start_local, duration_min):
    """Trajets à vélo connus — créneaux et durée habituels, en repli si aucun
    cours du planning ne correspond."""
    if not (6 <= duration_min <= 11):
        return False
    h, m = start_local.hour, start_local.minute
    if 9 <= h <= 10:
        return True
    if h == 13 and 15 <= m <= 45:
        return True
    if start_local.weekday() == 1:  # mardi
        if h == 19 and m <= 30:
            return True
        if (h == 20 and m >= 50) or (h == 21 and m <= 30):
            return True
    return False


def parse_fit(path, schedule, ref_cache, logged_cache):
    """Extrait une séance d'un fichier .fit, avec zones Karvonen et
    rattachement au planning réel."""
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
        # Les horodatages FIT sont en UTC ; conversion approximative heure
        # française d'été (+2h) pour le rattachement au planning et la
        # détection des trajets.
        start_local = start + datetime.timedelta(hours=2)

        matched_discipline = match_class(start_local, schedule, ref_cache)
        if not matched_discipline:
            matched_discipline = match_logged_session(start_local, logged_cache)
        if matched_discipline:
            activity_type = "seance"
        elif is_bike_trip(start_local, dur_s / 60):
            activity_type = "trajet"
        else:
            activity_type = "autre"

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
            "activity_type": activity_type,
            "matched_discipline": matched_discipline,
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
        matched = sum(1 for r in rows if r.get("matched_discipline"))
        print(f"{len(rows)} séance(s) synchronisée(s) vers Supabase ({matched} rattachée(s) à un cours du planning).")


if __name__ == "__main__":
    files = sorted(glob.glob(os.path.join(FIT_DIR, "*.fit")))
    if not files:
        print(f"Aucun fichier .fit dans {FIT_DIR}")
        sys.exit(0)
    schedule = load_schedule()
    ref_cache = {}
    logged_cache = {}
    rows = []
    for fp in files:
        row = parse_fit(fp, schedule, ref_cache, logged_cache)
        if row:
            rows.append(row)
    # Déduplique par start_time — Postgres refuse deux fois la même clé dans
    # un seul envoi.
    by_start = {}
    for r in rows:
        by_start[r["start_time"]] = r
    push(list(by_start.values()))
