#!/usr/bin/env python3
"""
Lit le Gadgetbridge.db et pousse un résumé quotidien (30 derniers jours)
directement dans la table `wearable_daily` de Supabase, via l'API REST.

Usage (dans Termux) :
  python sync_to_supabase.py /chemin/vers/Gadgetbridge.db

Nécessite : pip install requests
"""
import sqlite3
import json
import datetime
import struct
import sys
import requests

import mpb_auth

SUPABASE_URL = "https://zwltvhjitrvlrhbivdfm.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inp3bHR2aGppdHJ2bHJoYml2ZGZtIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODczMTMxMjcsImV4cCI6MjEwMjg4OTEyN30.hTD6h2r9dKdKaJ15vcFU6eN8WScA1rr_nT2dNByT6co"
# Jeton d'accès obtenu au démarrage (voir __main__) — la clé anon seule n'a
# plus accès aux données depuis le verrouillage des policies RLS.
ACCESS_TOKEN = None
DAYS = 30

DB_PATH = sys.argv[1] if len(sys.argv) > 1 else "Gadgetbridge.db"


def ms_to_date(ts_ms):
    return datetime.datetime.fromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d")


def build_daily_summary(db_path):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cutoff_ms = int(
        (datetime.datetime.now() - datetime.timedelta(days=DAYS)).timestamp() * 1000
    )

    days = {}

    def ensure_day(d):
        days.setdefault(d, {"day": d})

    cur.execute(
        "SELECT TIMESTAMP, HEART_RATE FROM HUAMI_HEART_RATE_RESTING_SAMPLE WHERE TIMESTAMP >= ?",
        (cutoff_ms,),
    )
    for ts, hr in cur.fetchall():
        d = ms_to_date(ts)
        ensure_day(d)
        days[d]["hr_resting"] = hr

    cur.execute(
        "SELECT TIMESTAMP, HEART_RATE FROM HUAMI_HEART_RATE_MAX_SAMPLE WHERE TIMESTAMP >= ?",
        (cutoff_ms,),
    )
    for ts, hr in cur.fetchall():
        d = ms_to_date(ts)
        ensure_day(d)
        days[d]["hr_max"] = hr

    try:
        cur.execute(
            "SELECT TIMESTAMP, PAI_LOW, PAI_MODERATE, PAI_HIGH FROM HUAMI_PAI_SAMPLE WHERE TIMESTAMP >= ?",
            (cutoff_ms,),
        )
        for ts, low, mod, high in cur.fetchall():
            d = ms_to_date(ts)
            ensure_day(d)
            days[d]["pai_low"] = low
            days[d]["pai_moderate"] = mod
            days[d]["pai_high"] = high
    except sqlite3.OperationalError:
        pass

    cur.execute(
        "SELECT TIMESTAMP, STRESS FROM HUAMI_STRESS_SAMPLE WHERE TIMESTAMP >= ?",
        (cutoff_ms,),
    )
    stress_by_day = {}
    for ts, stress in cur.fetchall():
        d = ms_to_date(ts)
        stress_by_day.setdefault(d, []).append(stress)
    for d, vals in stress_by_day.items():
        ensure_day(d)
        days[d]["stress_avg"] = round(sum(vals) / len(vals), 1)
        days[d]["stress_max"] = max(vals)

    cur.execute(
        "SELECT TIMESTAMP FROM HUAMI_SLEEP_SESSION_SAMPLE WHERE TIMESTAMP >= ?",
        (cutoff_ms,),
    )
    for (ts,) in cur.fetchall():
        d = ms_to_date(ts)
        ensure_day(d)
        days[d]["sleep_session_recorded"] = True

    cur.execute(
        "SELECT TIMESTAMP, VALUE FROM GENERIC_HRV_VALUE_SAMPLE WHERE TIMESTAMP >= ?",
        (cutoff_ms,),
    )
    hrv_by_day = {}
    for ts, val in cur.fetchall():
        d = ms_to_date(ts)
        hrv_by_day.setdefault(d, []).append(val)
    for d, vals in hrv_by_day.items():
        ensure_day(d)
        days[d]["hrv_avg"] = round(sum(vals) / len(vals), 1)

    cur.execute(
        "SELECT TIMESTAMP, RATE FROM HUAMI_SLEEP_RESPIRATORY_RATE_SAMPLE WHERE TIMESTAMP >= ? AND RATE > 0",
        (cutoff_ms,),
    )
    resp_by_day = {}
    for ts, rate in cur.fetchall():
        d = ms_to_date(ts)
        resp_by_day.setdefault(d, []).append(rate)
    for d, vals in resp_by_day.items():
        ensure_day(d)
        days[d]["resp_rate_avg"] = round(sum(vals) / len(vals), 1)

    # Table d'activité étendue : timestamps en SECONDES, HEART_RATE=255 = pas de mesure
    cutoff_s = cutoff_ms // 1000
    cur.execute(
        "SELECT TIMESTAMP, STEPS, RAW_INTENSITY, HEART_RATE FROM HUAMI_EXTENDED_ACTIVITY_SAMPLE WHERE TIMESTAMP >= ?",
        (cutoff_s,),
    )
    steps_by_day, intensity_by_day, hr_by_day = {}, {}, {}
    for ts, steps, intensity, hr in cur.fetchall():
        d = ms_to_date(ts * 1000)
        steps_by_day[d] = steps_by_day.get(d, 0) + (steps or 0)
        if intensity is not None:
            intensity_by_day.setdefault(d, []).append(intensity)
        if hr is not None and 0 < hr < 250:
            hr_by_day.setdefault(d, []).append(hr)

    for d in set(list(steps_by_day) + list(intensity_by_day) + list(hr_by_day)):
        ensure_day(d)
        days[d]["steps_total"] = steps_by_day.get(d, 0)
        if d in intensity_by_day:
            days[d]["intensity_avg"] = round(
                sum(intensity_by_day[d]) / len(intensity_by_day[d]), 1
            )
        if d in hr_by_day:
            days[d]["hr_avg"] = round(sum(hr_by_day[d]) / len(hr_by_day[d]), 1)
            days[d]["hr_max_activity"] = max(hr_by_day[d])

    # Vigilance par fenêtres horaires (matin/après-midi/soir) : moyennes VFC et
    # stress sur des créneaux fixes, pour comparer chaque jour à sa propre
    # référence sur le même créneau plutôt qu'une courbe théorique générique.
    # Créneaux ajustés sur les moments réellement immobiles de la journée
    # (14h-16h15 et 17h-19h hors mardi) — les précédents coïncidaient avec les
    # trajets à vélo, empêchant toute mesure fiable (le capteur optique au
    # poignet ne mesure quasiment que pendant l'immobilité).
    WINDOWS = [("morning", 6, 9), ("afternoon", 14, 16), ("evening", 17, 19)]
    cur.execute("SELECT TIMESTAMP, VALUE FROM GENERIC_HRV_VALUE_SAMPLE WHERE TIMESTAMP >= ?", (cutoff_ms,))
    hrv_rows = cur.fetchall()
    cur.execute("SELECT TIMESTAMP, STRESS FROM HUAMI_STRESS_SAMPLE WHERE TIMESTAMP >= ?", (cutoff_ms,))
    stress_rows = cur.fetchall()
    for label, lo, hi in WINDOWS:
        hrv_buckets, stress_buckets = {}, {}
        for ts, v in hrv_rows:
            if v is None or v <= 0:
                continue
            dt = datetime.datetime.fromtimestamp(ts / 1000)
            if lo <= dt.hour < hi:
                hrv_buckets.setdefault(dt.strftime("%Y-%m-%d"), []).append(v)
        for ts, v in stress_rows:
            if v is None or v < 0:
                continue
            dt = datetime.datetime.fromtimestamp(ts / 1000)
            if lo <= dt.hour < hi:
                stress_buckets.setdefault(dt.strftime("%Y-%m-%d"), []).append(v)
        for d, vals in hrv_buckets.items():
            ensure_day(d)
            days[d]["hrv_" + label] = round(sum(vals) / len(vals), 1)
        for d, vals in stress_buckets.items():
            ensure_day(d)
            days[d]["stress_" + label] = round(sum(vals) / len(vals), 1)

    conn.close()

    # PostgREST exige que tous les objets d'un même envoi aient exactement les
    # mêmes clés — on normalise chaque jour avec le même schéma complet (None
    # pour les champs absents).
    all_keys = set()
    for d in days.values():
        all_keys.update(d.keys())
    for d in days.values():
        for k in all_keys:
            d.setdefault(k, None)

    return list(days.values())


def push_to_supabase(rows):
    if not rows:
        print("Aucune donnée à envoyer.")
        return
    url = f"{SUPABASE_URL}/rest/v1/wearable_daily?on_conflict=day"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    resp = requests.post(url, headers=headers, data=json.dumps(rows))
    if resp.status_code >= 300:
        print(f"Erreur Supabase ({resp.status_code}): {resp.text}")
        sys.exit(1)
    print(f"{len(rows)} jours synchronisés vers Supabase.")


def build_sleep_stages(db_path, days=30):
    """Décode les stades de sommeil (léger/profond/paradoxal/réveils) depuis les
    8 derniers octets du blob HUAMI_SLEEP_SESSION_SAMPLE.DATA, au format
    4x uint16 little-endian dans l'ordre [paradoxal, léger, profond, réveils].
    Décodage vérifié manuellement contre les valeurs affichées par Zepp."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cutoff_ms = int((datetime.datetime.now() - datetime.timedelta(days=days)).timestamp() * 1000)
    cur.execute(
        "SELECT TIMESTAMP, DATA FROM HUAMI_SLEEP_SESSION_SAMPLE WHERE TIMESTAMP >= ? AND length(DATA) >= 8",
        (cutoff_ms,),
    )
    rows = []
    for ts, data in cur.fetchall():
        try:
            rem, light, deep, awake = struct.unpack("<HHHH", data[-8:])
        except struct.error:
            continue
        day = datetime.datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d")
        total = rem + light + deep
        if not (60 <= total <= 900):  # filtre les décodages visiblement aberrants
            continue
        rows.append({
            "day": day, "sleep_total_min": total, "sleep_light_min": light,
            "sleep_deep_min": deep, "sleep_rem_min": rem, "sleep_awake_count": awake,
            "_ts": ts,
        })
    conn.close()

    # Déduplique par jour (garde l'entrée la plus récente si plusieurs sessions
    # tombent sur la même date) — sinon Postgres refuse le upsert (ON CONFLICT
    # ne peut pas affecter la même ligne deux fois dans un même envoi).
    by_day = {}
    for r in rows:
        d = r["day"]
        if d not in by_day or r["_ts"] > by_day[d]["_ts"]:
            by_day[d] = r
    for r in by_day.values():
        # L'horodatage du blob correspond approximativement à l'heure de
        # finalisation de la session (proche du réveil) — utile pour estimer
        # la vigilance cognitive dans la journée (heures écoulées depuis le réveil).
        dt = datetime.datetime.fromtimestamp(r["_ts"] / 1000)
        r["wake_hour"] = round(dt.hour + dt.minute / 60, 2)
        del r["_ts"]

    return list(by_day.values())


def push_sleep_stages(rows):
    if not rows:
        return
    url = f"{SUPABASE_URL}/rest/v1/wearable_daily?on_conflict=day"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    resp = requests.post(url, headers=headers, data=json.dumps(rows))
    if resp.status_code >= 300:
        print(f"Erreur Supabase (sommeil) ({resp.status_code}): {resp.text}")
    else:
        print(f"{len(rows)} nuits (stades de sommeil) synchronisées.")


if __name__ == "__main__":
    ACCESS_TOKEN = mpb_auth.get_access_token(SUPABASE_URL, SUPABASE_KEY)
    # Séances (zones cardiaques, effet d'entraînement) : plus estimées ici — la
    # sauvegarde automatique Zepp vers Google Drive fournit désormais ces données
    # officielles, lues directement par Claude depuis Drive.
    rows = build_daily_summary(DB_PATH)
    push_to_supabase(rows)
    sleep_rows = build_sleep_stages(DB_PATH)
    push_sleep_stages(sleep_rows)
