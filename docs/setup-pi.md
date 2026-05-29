# Setup auf dem Raspberry Pi

Schritt-für-Schritt von einem frisch installierten Raspberry Pi OS Lite
(64-bit) zu einem laufenden Loxone-Voice-Service.

**Ziel-Topologie:**

```
[ Telegram-App ]                    [ Internet ]
       │                                  │
       │ Long-Poll outbound               │ Anthropic API outbound
       └─────────┬────────────────────────┘
                 │
          ┌──────▼──────────────┐         ┌────────────────────┐
          │  Raspberry Pi 4/5   │ ◀──────▶│ Loxone Miniserver  │
          │  192.168.1.32       │  WSS    │ 192.168.1.87       │
          │  loxone-voice       │  443    │ (self-signed cert) │
          └─────────────────────┘         └────────────────────┘
```

Keine eingehenden Ports nötig — Telegram-Updates kommen per Long-Polling
**outbound** vom Pi, Anthropic & der Miniserver werden ebenfalls
outbound erreicht.

## Schnellstart (ein Befehl)

Voraussetzung: `voice-app`-User in Loxone Config angelegt (siehe
Abschnitt 1 unten), Telegram-Bot beim `@BotFather` registriert
(Abschnitt 2), Anthropic-Key in der Hand (Abschnitt 3).

Dann auf dem Pi per SSH:

```bash
curl -fsSL https://raw.githubusercontent.com/dfreib007/Loxone/claude/loxone-voice-control-WNWBH/scripts/setup-pi.sh | bash
```

Das Script:

1. Installiert Docker + Compose, falls noch nicht da
2. Klont das Repo nach `~/loxone-voice`
3. Fragt deine Secrets **interaktiv** ab (Passwörter mit verstecktem Input)
4. Schreibt `.env` mit `chmod 600`
5. Startet den Container und prüft, ob der Miniserver-Handshake durchläuft

Bei Erfolg siehst du am Ende „Miniserver handshake succeeded". Wenn
nicht, druckt das Script die letzten 40 Log-Zeilen und zeigt die
häufigsten Fixe.

**Erneuter Lauf des Scripts** = Update: es zieht den neuesten Code und
startet den Container neu, lässt die `.env` aber in Ruhe (Bestätigung
ist eingebaut).

Falls du es lieber Schritt für Schritt manuell machst, gehe weiter mit
den Abschnitten unten.

---

## 1. App-User im Loxone Config anlegen

Erstelle einen separaten User für den Voice-Service, **nicht den
Admin-User**. Damit gilt: selbst wenn der Pi kompromittiert wäre,
kann ein Angreifer nichts editieren, nur Geräte schalten.

1. Loxone Config öffnen, verbinden zu `192.168.1.87`.
2. Benutzerverwaltung → **Neuer Benutzer**
   - Benutzername: `voice-app`
   - Passwort: 24+ Zeichen, Passwort-Manager-generiert
   - Rolle: **„Bedienen"** (nicht „Administrator")
3. Rechte einschränken: in **Berechtigungen** nur die Räume / Kategorien
   freigeben, die Claude steuern darf. Faustregel: Lichter und Rollos ja,
   Heizung und Alarm zuerst sperren, später nachjustieren.
4. Config in den Miniserver speichern.

## 2. Telegram-Bot anlegen

1. Telegram-App öffnen, `@BotFather` suchen → `/newbot`.
2. Name vergeben (z. B. `Mein Haus`), dann den Username (`xyz_bot`).
3. BotFather zeigt einen **Bot-Token** (`123456:ABC-DEF…`). Den brauchst
   du gleich für die `.env`. **Nicht im Chat teilen, nicht ins Repo.**
4. Deine eigene Telegram-User-ID herausfinden: einmal an
   `@userinfobot` schreiben → er antwortet mit deiner numerischen ID.

## 3. Anthropic API-Key

1. Auf [console.anthropic.com](https://console.anthropic.com) einloggen.
2. **API Keys** → **Create Key**, Limit setzen (z. B. $20/Monat).
3. Key kopieren — wird nur einmal angezeigt.

## 4. Manueller Setup (alternativ zum Schnellstart)

Voraussetzung: Pi 4 oder 5, 64-bit Raspberry Pi OS, im selben LAN wie der
Miniserver, SSH aktiv.

```bash
# Vom Mac/Laptop aus auf den Pi:
ssh pi@192.168.1.32

# Auf dem Pi:
sudo apt update && sudo apt install -y docker.io docker-compose-plugin git
sudo usermod -aG docker $USER
exit  # einmal aus- und wieder einloggen, damit die Gruppe greift
```

## 5. Projekt auf den Pi klonen

```bash
ssh pi@192.168.1.32
cd ~
git clone https://github.com/dfreib007/Loxone.git loxone-voice
cd loxone-voice
git checkout claude/loxone-voice-control-WNWBH
```

## 6. `.env` direkt auf dem Pi anlegen

**Wichtig:** Diese Datei niemals woanders schreiben oder verschicken.
Sie lebt nur auf dem Pi.

```bash
cp .env.example .env
nano .env
```

Folgende Werte eintragen:

```ini
# --- Loxone Miniserver ---
LOXONE_HOST=192.168.1.87
LOXONE_USE_HTTPS=true
LOXONE_VERIFY_TLS=false
LOXONE_USER=voice-app
LOXONE_PASSWORD=<dein-langes-passwort-aus-schritt-1>

# --- Anthropic (Claude) ---
ANTHROPIC_API_KEY=sk-ant-<dein-key-aus-schritt-3>
ANTHROPIC_MODEL=claude-opus-4-7

# --- Telegram ---
TELEGRAM_BOT_TOKEN=<token-aus-schritt-2>
TELEGRAM_ALLOWED_USER_IDS=<deine-numerische-id-aus-schritt-2>

# --- Runtime ---
LOG_LEVEL=INFO
AUDIT_LOG_PATH=/app/data/audit.jsonl
```

Speichern (`Ctrl+O`, `Enter`, `Ctrl+X`), dann Berechtigungen einschränken:

```bash
chmod 600 .env
ls -la .env       # sollte "-rw------- 1 pi pi" anzeigen
```

## 7. Erster Start

```bash
docker compose up --build -d
docker compose logs -f loxone-voice
```

Erwartete erste Log-Zeilen (sinngemäß):

```
INFO  loxone_voice.adapter.client: connecting to wss://192.168.1.87:443/ws/rfc6455
INFO  loxone_voice.adapter.client: handshake complete
INFO  loxone_voice.gateway.telegram_bot: starting Telegram long-polling
```

Im Telegram-App jetzt `/start` an deinen Bot. Du solltest die
Begrüßungs-Nachricht zurückbekommen.

## 8. Smoke-Test

Schick dem Bot:

```
Welche Räume gibt es?
```

Er sollte deine Räume aus dem Miniserver listen. Wenn ja → läuft.

Dann etwas Konkretes:

```
Mach das Wohnzimmerlicht aus
```

Wenn das Licht wirklich aus geht: Glückwunsch, dein Smart Home spricht
mit dir.

## 9. Auto-Start nach Reboot

Docker Compose mit `restart: unless-stopped` ist bereits konfiguriert.
Damit Docker selbst beim Boot startet:

```bash
sudo systemctl enable docker
```

Nach `sudo reboot` sollte der Bot ohne weitere Aktion wieder erreichbar
sein.

## 10. Updates einspielen

```bash
cd ~/loxone-voice
git pull
docker compose up --build -d
docker compose logs -f loxone-voice
```

## Troubleshooting

| Symptom | Ursache & Fix |
|---|---|
| `getPublicKey` schlägt fehl, TLS-Fehler | Selbst-signiertes Cert. `LOXONE_VERIFY_TLS=false` in `.env` checken. |
| Bot antwortet „nicht freigeschaltet" | `TELEGRAM_ALLOWED_USER_IDS` enthält deine ID nicht. Werte vergleichen, dann `docker compose restart`. |
| `getkey2` mit Code 401 | Falsches Passwort oder User. Im Loxone Config einmal mit den Creds einloggen, um sie zu verifizieren. |
| Claude antwortet „Ich kann das nicht" auf alles | Der `voice-app`-User hat keine Rechte auf die fraglichen Räume. Im Loxone Config nachjustieren. |
| Bot reagiert gar nicht | `docker compose logs loxone-voice` lesen. Wenn `Telegram polling failed`: Token oder Internet-Verbindung prüfen. |

## Audit-Log inspizieren

Jede Aktion landet als JSONL-Zeile im Audit-Log:

```bash
tail -f ~/loxone-voice/data/audit.jsonl | jq .
```

User-IDs sind gehasht, aber Befehle und Tool-Aufrufe sind im Klartext —
also auch der Audit-Log gehört nicht ins Internet.

## Was, wenn ich meine Tokens leake?

Sofort rotieren:

- **Loxone:** Im Config alten `voice-app`-User löschen, neuen anlegen, `.env` aktualisieren.
- **Telegram:** `@BotFather` → `/revoke` → neuen Token in `.env`.
- **Anthropic:** Konsole → alten Key revoken → neuen erstellen.

Dann `docker compose restart`.
