#!/usr/bin/env python3
"""Synchronise les données quotidiennes directement depuis le cloud Zepp vers
Supabase, sans passer par Gadgetbridge / Google Drive / rclone / Termux.

Remplace, pour les colonnes qu'il couvre, le rôle de sync_to_supabase.py.
Ne tourne plus forcément sur le téléphone de Sylvain : n'importe quelle
machine avec un accès internet peut l'exécuter (serveur, tâche planifiée...).

Authentification : login email/mot de passe reverse-engineeré (API non
officielle Zepp/Huami — cf. README, section "Pipeline Zepp cloud"). Testé et
validé champ par champ contre les valeurs déjà présentes dans wearable_daily
(voir les commentaires "Validé" ci-dessous et le message de session sur la
branche feat/zepp-cloud-sync).

Usage :
  ZEPP_EMAIL=... ZEPP_PASSWORD=... python sync_zepp_cloud.py [--days 14]

Ne JAMAIS committer d'email/mot de passe en dur dans ce fichier — toujours
via variables d'environnement.

LIMITATIONS CONNUES (voir README) :
- stress_avg / stress_max : PAS DÉCODABLES avec un effort raisonnable.
  L'eventType "Charge/stress_data" (celui qui semblait le bon candidat) est
  un protobuf imbriqué (sans schéma public) contenant l'état interne brut de
  l'algorithme de stress (features PPG/mouvement) — aucun des champs décodés
  ne corrèle avec stress_avg/stress_max réels (testé sur 3 jours, tous les
  champs numériques extraits, aucune corrélation). Aucun eventType candidat
  plus simple trouvé (Stress, StressHealthInfo, StressInfo, AllDayStress,
  StressScore testés : tous vides). Ce script n'écrit donc PAS ces colonnes,
  pour ne pas écraser les valeurs correctes déjà poussées par
  sync_to_supabase.py (Gadgetbridge).
Tant que ce point n'est pas résolu, faire tourner ce script EN PARALLÈLE de
sync_to_supabase.py (pas à sa place), et laisser Gadgetbridge alimenter le
stress. La VFC (hrv_avg + fenêtres matin/après-midi/soir), elle, EST
décodée et validée ci-dessous (endpoint HRVRMSSD/real_data, ~800-1000
mesures/jour — l'endpoint hrv_sdnn/real_data initialement essayé était le
mauvais candidat, il ne renvoie que 2-4 mesures nocturnes).
"""
import argparse
import base64
import json
import os
import sys
import urllib.parse
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

SUPABASE_URL = "https://zwltvhjitrvlrhbivdfm.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inp3bHR2aGppdHJ2bHJoYml2ZGZtIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODczMTMxMjcsImV4cCI6MjEwMjg4OTEyN30.hTD6h2r9dKdKaJ15vcFU6eN8WScA1rr_nT2dNByT6co"

LOCAL_TZ = ZoneInfo("Europe/Paris")

# Constantes publiques de l'appli Zepp (pas des secrets Sylvain — présentes
# en dur dans l'APK officiel, extraites par la communauté reverse-engineering).
ZEPP_AES_KEY = b"xeNtBVqzDc6tuNTh"
ZEPP_AES_IV = b"MAAAYAAAAAAAAABg"
ZEPP_TOKENS_URL = "https://api-user-us2.zepp.com/v2/registrations/tokens"
ZEPP_LOGIN_URL = "https://api-mifit-us2.zepp.com/v2/client/login"

# Host régional découvert empiriquement pour ce compte (région FR -> de2).
# Si Sylvain change de pays/région Zepp, refaire un test avec les autres
# hosts candidats (api-mifit.zepp.com, api-mifit-us2/us3.zepp.com...).
DEFAULT_HOST = os.environ.get("ZEPP_HOST", "api-mifit-de2.zepp.com")


def zepp_login(email: str, password: str) -> tuple[str, str, str]:
    """Login complet email+mot de passe. Retourne (app_token, user_id, host)."""
    payload = {
        "emailOrPhone": email,
        "state": "REDIRECTION",
        "client_id": "HuaMi",
        "password": password,
        "redirect_uri": "https://s3-us-west-2.amazonaws.com/hm-registration/successsignin.html",
        "region": "us-west-2",
        "token": ["access", "refresh"],
        "country_code": "FR",
    }
    encoded = urllib.parse.urlencode(payload, doseq=True).encode()
    cipher = AES.new(ZEPP_AES_KEY, AES.MODE_CBC, iv=ZEPP_AES_IV)
    encrypted = cipher.encrypt(pad(encoded, AES.block_size))

    r1 = requests.post(
        ZEPP_TOKENS_URL,
        data=encrypted,
        headers={
            "app_name": "com.huami.midong",
            "appname": "com.huami.midong",
            "cv": "151689_9.12.5",
            "v": "2.0",
            "appplatform": "android_phone",
            "vn": "9.12.5",
            "user-agent": "Zepp/9.12.5 (Pixel 4; Android 12; Density/2.75)",
            "x-hm-ekv": "1",
            "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
        },
        allow_redirects=False,
        timeout=20,
    )
    if r1.status_code != 303:
        raise RuntimeError(f"Login échoué (étape 1) : status {r1.status_code} — {r1.text[:300]}")
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(r1.headers.get("Location", "")).query)
    access_token = qs.get("access", [None])[0]
    if not access_token:
        raise RuntimeError("Login échoué (étape 1) : pas de token 'access' dans la redirection")

    r2 = requests.post(
        ZEPP_LOGIN_URL,
        data={
            "code": access_token,
            "device_id": str(uuid.uuid4()),
            "device_model": "android_phone",
            "app_version": "9.12.5",
            "third_name": "huami",
            "source": "com.huami.watch.hmwatchmanager:9.12.5:151689",
            "app_name": "com.huami.midong",
            "country_code": "FR",
            "grant_type": "access_token",
            "allow_registration": "false",
            "lang": "fr",
        },
        headers={
            "app_name": "com.huami.webapp",
            "user-agent": "Mozilla/5.0 (X11; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0",
            "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
        },
        timeout=20,
    )
    if r2.status_code != 200:
        raise RuntimeError(f"Login échoué (étape 2) : status {r2.status_code} — {r2.text[:300]}")
    token_info = r2.json().get("token_info", {})
    app_token = token_info.get("app_token")
    user_id = token_info.get("user_id")
    if not app_token or not user_id:
        raise RuntimeError(f"Login échoué (étape 2) : réponse inattendue {r2.text[:300]}")
    return app_token, str(user_id), DEFAULT_HOST


def zepp_get(host: str, app_token: str, path: str, params: dict) -> dict:
    headers = {
        "apptoken": app_token,
        "appname": "com.huami.midong",
        "appplatform": "ios_phone",
        "accept": "*/*",
        "v": "2.0",
        "lang": "fr",
        "timezone": "Europe/Paris",
        "user-agent": "Zepp/10.2.5 (iPhone; iOS 26.3.1; Scale/3.00)",
    }
    q = {"r": str(uuid.uuid4()).upper(), **params}
    r = requests.get(f"https://{host}{path}", params=q, headers=headers, timeout=20)
    r.raise_for_status()
    return r.json()


def fetch_band_data(host, app_token, user_id, from_d: date, to_d: date) -> dict:
    """Sommeil (stades, réveils) + pas + FC repos. Validé champ par champ
    (dp=deep, lt=light, dt=rem, wk=wake_minutes, rhr=hr_resting) contre les
    valeurs déjà présentes dans wearable_daily — correspondance exacte sur
    4 jours testés."""
    data = zepp_get(host, app_token, "/v1/data/band_data.json", {
        "userid": user_id, "from_date": from_d.isoformat(), "to_date": to_d.isoformat(),
        "query_type": "detail", "byteLength": 8, "device_type": 0,
    })
    rows = {}
    for entry in data.get("data", []):
        day = entry["date_time"]
        try:
            summary = json.loads(base64.b64decode(entry["summary"]))
        except Exception:
            continue
        slp = summary.get("slp")
        stp = summary.get("stp")
        row = {"day": day}
        if slp:
            row["sleep_deep_min"] = slp.get("dp")
            row["sleep_light_min"] = slp.get("lt")
            row["sleep_rem_min"] = slp.get("dt")
            row["sleep_awake_count"] = slp.get("wk")
            row["sleep_total_min"] = (slp.get("dt", 0) or 0) + (slp.get("lt", 0) or 0) + (slp.get("dp", 0) or 0)
            row["hr_resting"] = slp.get("rhr")
            ed = slp.get("ed")
            if ed:
                dt = datetime.fromtimestamp(ed, tz=LOCAL_TZ)
                row["wake_hour"] = round(dt.hour + dt.minute / 60, 2)
        if stp:
            row["steps_total"] = stp.get("ttl")
        rows[day] = row
    return rows


def fetch_pai(host, app_token, user_id, from_ms: int, to_ms: int) -> dict:
    """PAI par zone. Validé : correspondance exacte (aux arrondis flottants
    près) sur 4 jours testés, une fois le bucket jour recalé sur Europe/Paris
    (le champ 'time' est en UTC ms, pas déjà en jour local)."""
    data = zepp_get(host, app_token, f"/users/{user_id}/events", {
        "eventType": "PaiHealthInfo", "from": from_ms, "to": to_ms,
        "limit": 200, "reverse": 1, "userId": user_id,
    })
    rows = {}
    for item in data.get("items", []):
        try:
            t = int(item["time"])
        except (KeyError, ValueError, TypeError):
            continue
        day = datetime.fromtimestamp(t / 1000, tz=LOCAL_TZ).strftime("%Y-%m-%d")
        rows[day] = {
            "day": day,
            "pai_low": float(item.get("lowZonePai") or 0),
            "pai_moderate": float(item.get("mediumZonePai") or 0),
            "pai_high": float(item.get("highZonePai") or 0),
        }
    return rows


def fetch_respiratory(host, app_token, from_ms: int, to_ms: int) -> dict:
    """Fréquence respiratoire : un octet par minute (0 = pas de mesure).
    Validé : écart <= 0.3 respiration/min contre wearable_daily.resp_rate_avg
    sur 4 jours testés (source/algo légèrement différents de Gadgetbridge,
    d'où le petit écart résiduel)."""
    data = zepp_get(host, app_token, "/v2/users/me/events", {
        "eventType": "RespiratoryRate", "subType": "real_data",
        "from": from_ms, "to": to_ms, "limit": 200, "reverse": 1,
    })
    rows = {}
    for item in data.get("items", []):
        try:
            t = int(item["timestamp"])
        except (KeyError, ValueError, TypeError):
            continue
        day = datetime.fromtimestamp(t / 1000, tz=LOCAL_TZ).strftime("%Y-%m-%d")
        raw = base64.b64decode(item["value"]["measurements"])
        vals = [b for b in raw if 0 < b < 60]
        if vals:
            rows[day] = {"day": day, "resp_rate_avg": round(sum(vals) / len(vals), 1)}
    return rows


HRV_WINDOWS = [("morning", 6, 9), ("afternoon", 13, 15), ("evening", 19, 21)]


def fetch_hrv(host, app_token, from_ms: int, to_ms: int) -> dict:
    """VFC (RMSSD) : ~800-1000 mesures/jour, une par minute environ.
    Validé : moyenne journalière à ~1 unité près de wearable_daily.hrv_avg
    sur 4 jours testés. Comme en prod actuellement, les échantillons sont
    concentrés le matin (le capteur ne mesure pas en continu l'après-midi/
    soir) — les fenêtres afternoon/evening resteront donc creuses ici aussi,
    ce n'est pas un défaut du décodage.
    Piège evité : l'eventType 'hrv_sdnn/real_data' (testé en premier, plus
    intuitif vu son nom) ne renvoie que 2-4 mesures nocturnes/jour — c'est
    'HRVRMSSD/real_data' qui est la bonne source, découvert en comparant
    plusieurs presets d'un autre outil (zepp-health-cli)."""
    data = zepp_get(host, app_token, "/v2/users/me/events", {
        "eventType": "HRVRMSSD", "subType": "real_data",
        "from": from_ms, "to": to_ms, "limit": 200, "reverse": 1,
    })
    by_day = {}
    for item in data.get("items", []):
        start = item.get("value", {}).get("startTime")
        if not start:
            continue
        for s in item["value"].get("samples", []):
            hrv = s.get("hrv")
            if not hrv:
                continue
            dt = datetime.fromtimestamp((start + s["s"]) / 1000, tz=LOCAL_TZ)
            day = dt.strftime("%Y-%m-%d")
            by_day.setdefault(day, []).append((dt.hour, hrv))

    rows = {}
    for day, samples in by_day.items():
        vals = [v for _, v in samples]
        row = {"day": day, "hrv_avg": round(sum(vals) / len(vals), 1)}
        for label, lo, hi in HRV_WINDOWS:
            w = [v for h, v in samples if lo <= h < hi]
            if w:
                row[f"hrv_{label}"] = round(sum(w) / len(w), 1)
        rows[day] = row
    return rows


def push_to_supabase(rows: list[dict]) -> None:
    if not rows:
        print("Aucune donnée à envoyer.")
        return
    all_keys = set()
    for r in rows:
        all_keys.update(r.keys())
    for r in rows:
        for k in all_keys:
            r.setdefault(k, None)

    resp = requests.post(
        f"{SUPABASE_URL}/rest/v1/wearable_daily?on_conflict=day",
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=minimal",
        },
        data=json.dumps(rows),
        timeout=30,
    )
    if resp.status_code >= 300:
        print(f"Erreur Supabase ({resp.status_code}): {resp.text}")
        sys.exit(1)
    print(f"{len(rows)} jours synchronisés vers Supabase (source: cloud Zepp).")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--dry-run", action="store_true", help="N'écrit pas dans Supabase, affiche juste ce qui serait envoyé")
    args = parser.parse_args()

    email = os.environ.get("ZEPP_EMAIL")
    password = os.environ.get("ZEPP_PASSWORD")
    if not email or not password:
        sys.exit("ZEPP_EMAIL et ZEPP_PASSWORD doivent être définis en variables d'environnement.")

    app_token, user_id, host = zepp_login(email, password)
    print(f"Connecté (user_id={user_id}, host={host})")

    today = date.today()
    from_d = today - timedelta(days=args.days)
    from_ms = int(datetime.combine(from_d, datetime.min.time(), tzinfo=LOCAL_TZ).timestamp() * 1000)
    to_ms = int(datetime.combine(today + timedelta(days=1), datetime.min.time(), tzinfo=LOCAL_TZ).timestamp() * 1000)

    band = fetch_band_data(host, app_token, user_id, from_d, today)
    pai = fetch_pai(host, app_token, user_id, from_ms, to_ms)
    resp = fetch_respiratory(host, app_token, from_ms, to_ms)
    hrv = fetch_hrv(host, app_token, from_ms, to_ms)

    merged = {}
    for source in (band, pai, resp, hrv):
        for day, fields in source.items():
            merged.setdefault(day, {"day": day}).update(fields)

    rows = list(merged.values())
    if args.dry_run:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
    else:
        push_to_supabase(rows)


if __name__ == "__main__":
    main()
