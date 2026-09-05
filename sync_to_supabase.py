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
# Rétention des échantillons par minute bruts : au-delà, seules les agrégats
# quotidiens (wearable_daily) et les interprétations dérivées (bedtime_hour,
# wake_hour, hr_max_activity...) sont conservés indéfiniment.
MINUTE_RETENTION_DAYS = 3

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
            # FC minimale réelle (instantanée), distincte de hr_resting qui est
            # l'estimation propriétaire de la montre — même source que
            # hr_max_activity, pour avoir un vrai minimum/maximum symétriques.
            days[d]["hr_min"] = min(hr_by_day[d])

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
    Décodage vérifié manuellement contre les valeurs affichées par Zepp.

    Les octets 0-3 du blob dupliquent la colonne TIMESTAMP de la ligne (fin
    de session / réveil). Les octets 4-7 avaient été pris pour une heure de
    coucher, mais vérification faite sur 10 nuits différentes (dump
    hexadécimal), c'est une valeur FIXE à 22:00:00 chaque nuit, quelle que
    soit l'heure réelle de coucher — un paramètre interne de l'algorithme du
    bracelet (borne de fenêtre d'analyse), pas une donnée mesurée. Ne pas
    l'utiliser comme heure de coucher."""
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
            "_ts": ts, "_total": total,
        })
    conn.close()

    # Déduplique par jour en gardant la session la plus LONGUE, pas la plus
    # récente : Gadgetbridge peut logguer plusieurs sessions le même jour
    # (une sieste l'après-midi, une session ré-écrite plusieurs fois pendant
    # la nuit pendant qu'elle progresse) — prendre "la plus récente"
    # laissait une sieste courte mais tardive écraser la vraie nuit
    # (c'est très probablement l'origine d'une "heure de réveil" à 17h37
    # observée en pratique). La session la plus longue est presque toujours
    # la vraie nuit, jamais une sieste.
    by_day = {}
    for r in rows:
        d = r["day"]
        if d not in by_day or r["_total"] > by_day[d]["_total"]:
            by_day[d] = r
    for r in by_day.values():
        dt_wake = datetime.datetime.fromtimestamp(r["_ts"] / 1000)
        r["wake_hour"] = round(dt_wake.hour + dt_wake.minute / 60, 2)
        del r["_ts"], r["_total"]

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


def build_minute_samples(db_path, days=MINUTE_RETENTION_DAYS):
    """Lit HUAMI_EXTENDED_ACTIVITY_SAMPLE (une ligne par minute) sur la fenêtre de
    rétention, sans agréger : c'est la donnée brute que build_daily_summary()
    résume puis jette. Conservée séparément (courte durée) pour les courbes
    intra-journée et la dérivation de l'heure de coucher ci-dessous."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cutoff_s = int((datetime.datetime.now() - datetime.timedelta(days=days)).timestamp())
    cur.execute(
        "SELECT TIMESTAMP, STEPS, RAW_INTENSITY, HEART_RATE FROM HUAMI_EXTENDED_ACTIVITY_SAMPLE "
        "WHERE TIMESTAMP >= ? ORDER BY TIMESTAMP",
        (cutoff_s,),
    )
    rows = []
    for ts, steps, intensity, hr in cur.fetchall():
        dt = datetime.datetime.fromtimestamp(ts)
        rows.append({
            "ts": dt.isoformat(),
            "day": dt.strftime("%Y-%m-%d"),
            "heart_rate": hr if (hr is not None and 0 < hr < 250) else None,
            "steps": steps,
            "intensity": intensity,
            "_ts_epoch": ts,
        })
    conn.close()
    return rows


def push_minute_samples(rows):
    if not rows:
        return
    url = f"{SUPABASE_URL}/rest/v1/wearable_minute_samples?on_conflict=ts"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    payload = [
        {"ts": r["ts"], "day": r["day"], "heart_rate": r["heart_rate"], "steps": r["steps"], "intensity": r["intensity"]}
        for r in rows
    ]
    CHUNK = 2000  # évite un unique payload de plusieurs milliers de lignes
    for i in range(0, len(payload), CHUNK):
        resp = requests.post(url, headers=headers, data=json.dumps(payload[i:i + CHUNK]))
        if resp.status_code >= 300:
            print(f"Erreur Supabase (minute) ({resp.status_code}): {resp.text}")
            return
    print(f"{len(payload)} échantillons par minute synchronisés.")


def build_hrv_minute_samples(db_path, days=MINUTE_RETENTION_DAYS):
    """Lit GENERIC_HRV_VALUE_SAMPLE (une mesure réelle à son horodatage propre,
    pas calée sur la minute) sur la fenêtre de rétention. Jusqu'ici seule une
    moyenne par jour (hrv_avg) ou par créneau fixe (hrv_morning/afternoon/
    evening) était conservée ; ces lectures individuelles permettent de voir
    la VFC évoluer dans la journée plutôt qu'une poignée de valeurs figées."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cutoff_ms = int((datetime.datetime.now() - datetime.timedelta(days=days)).timestamp() * 1000)
    cur.execute(
        "SELECT TIMESTAMP, VALUE FROM GENERIC_HRV_VALUE_SAMPLE WHERE TIMESTAMP >= ?",
        (cutoff_ms,),
    )
    rows = []
    for ts, val in cur.fetchall():
        if val is None or val <= 0:
            continue
        dt = datetime.datetime.fromtimestamp(ts / 1000)
        rows.append({"ts": dt.isoformat(), "day": dt.strftime("%Y-%m-%d"), "hrv": val})
    conn.close()
    return rows


def build_stress_minute_samples(db_path, days=MINUTE_RETENTION_DAYS):
    """Lit HUAMI_STRESS_SAMPLE à son horodatage propre sur la fenêtre de
    rétention — même principe que build_hrv_minute_samples, pour voir le
    stress évoluer dans la journée plutôt qu'une seule moyenne quotidienne
    ou les 3 créneaux fixes (stress_morning/afternoon/evening)."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cutoff_ms = int((datetime.datetime.now() - datetime.timedelta(days=days)).timestamp() * 1000)
    cur.execute(
        "SELECT TIMESTAMP, STRESS FROM HUAMI_STRESS_SAMPLE WHERE TIMESTAMP >= ?",
        (cutoff_ms,),
    )
    rows = []
    for ts, val in cur.fetchall():
        if val is None or val < 0:
            continue
        dt = datetime.datetime.fromtimestamp(ts / 1000)
        rows.append({"ts": dt.isoformat(), "day": dt.strftime("%Y-%m-%d"), "stress": val})
    conn.close()
    return rows


def push_wearable_minute_rows(rows, label):
    """Pousse des lignes partielles (ts + day + une seule métrique) dans
    wearable_minute_samples ; merge-duplicates ne touche que les colonnes
    fournies dans le JSON, donc complète une ligne existante (FC/pas/
    intensité) ou en crée une nouvelle (le reste à null) plutôt que
    d'écraser quoi que ce soit — utilisé pour VFC et stress, mesurés à
    leur propre rythme (quelques fois par jour), pas au rythme de la minute."""
    if not rows:
        return
    url = f"{SUPABASE_URL}/rest/v1/wearable_minute_samples?on_conflict=ts"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    resp = requests.post(url, headers=headers, data=json.dumps(rows))
    if resp.status_code >= 300:
        print(f"Erreur Supabase ({label} minute) ({resp.status_code}): {resp.text}")
        return
    print(f"{len(rows)} lectures {label} horodatées synchronisées.")


def purge_old_minute_samples():
    """Supprime les échantillons par minute plus vieux que MINUTE_RETENTION_DAYS.
    Ne touche jamais wearable_daily : les agrégats et interprétations
    (hr_max_activity, wake_hour, bedtime_hour...) restent en place."""
    cutoff_day = (datetime.datetime.now() - datetime.timedelta(days=MINUTE_RETENTION_DAYS)).strftime("%Y-%m-%d")
    url = f"{SUPABASE_URL}/rest/v1/wearable_minute_samples?day=lt.{cutoff_day}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Prefer": "return=minimal",
    }
    resp = requests.delete(url, headers=headers)
    if resp.status_code >= 300:
        print(f"Erreur Supabase (purge minute) ({resp.status_code}): {resp.text}")
    else:
        print(f"Échantillons par minute antérieurs au {cutoff_day} purgés.")


def derive_bedtimes(minute_rows, wake_rows):
    """Estime l'heure de coucher réelle à partir de l'immobilité (pas + intensité)
    qui précède directement le réveil détecté (wake_rows, cf. build_sleep_stages),
    plutôt que d'utiliser la valeur fixe 22:00 du blob de sommeil (cf.
    build_sleep_stages, docstring). Ne renvoie une valeur que si au moins 3h
    d'immobilité quasi continue précèdent le réveil — sinon on ne devine pas."""
    by_day_wake = {r["day"]: r for r in wake_rows if r.get("wake_hour") is not None}
    window_all = sorted(minute_rows, key=lambda r: r["_ts_epoch"])
    results = []
    for day, wake in by_day_wake.items():
        wake_epoch = (datetime.datetime.strptime(day, "%Y-%m-%d") + datetime.timedelta(hours=wake["wake_hour"])).timestamp()
        window_start = wake_epoch - 14 * 3600  # ne remonte pas au-delà de 14h avant le réveil
        window = [r for r in window_all if window_start <= r["_ts_epoch"] <= wake_epoch]
        if len(window) < 60:
            continue
        still = [(r["steps"] in (0, None)) and ((r["intensity"] or 0) <= 5) for r in window]
        # Remonte depuis le réveil ; tolère de courts réveils nocturnes (<=5 min
        # d'activité d'affilée) mais s'arrête dès qu'une plage plus longue rompt
        # l'immobilité — ça marque la fin de la nuit (le coucher).
        bed_idx, consecutive_active = None, 0
        for idx in range(len(window) - 1, -1, -1):
            if still[idx]:
                bed_idx = idx
                consecutive_active = 0
            else:
                consecutive_active += 1
                if consecutive_active > 5:
                    break
        if bed_idx is None:
            continue
        duration_min = (window[-1]["_ts_epoch"] - window[bed_idx]["_ts_epoch"]) / 60
        if duration_min < 180:
            continue
        bed_dt = datetime.datetime.fromtimestamp(window[bed_idx]["_ts_epoch"])
        results.append({"day": day, "bedtime_hour": round(bed_dt.hour + bed_dt.minute / 60, 2)})
    return results


if __name__ == "__main__":
    ACCESS_TOKEN = mpb_auth.get_access_token(SUPABASE_URL, SUPABASE_KEY)
    # Séances (zones cardiaques, effet d'entraînement) : plus estimées ici — la
    # sauvegarde automatique Zepp vers Google Drive fournit désormais ces données
    # officielles, lues directement par Claude depuis Drive.
    rows = build_daily_summary(DB_PATH)
    push_to_supabase(rows)
    sleep_rows = build_sleep_stages(DB_PATH)
    push_sleep_stages(sleep_rows)

    minute_rows = build_minute_samples(DB_PATH)
    push_minute_samples(minute_rows)
    hrv_minute_rows = build_hrv_minute_samples(DB_PATH)
    push_wearable_minute_rows(hrv_minute_rows, "VFC")
    stress_minute_rows = build_stress_minute_samples(DB_PATH)
    push_wearable_minute_rows(stress_minute_rows, "stress")
    bedtime_rows = derive_bedtimes(minute_rows, sleep_rows)
    push_sleep_stages(bedtime_rows)
    purge_old_minute_samples()
