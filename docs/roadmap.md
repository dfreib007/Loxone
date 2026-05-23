# Roadmap

Phasenplan vom leeren Repo bis zur Production-Lösung. Aufwände sind grobe
Richtwerte für eine Person bei ~10 h/Woche Einsatz.

## Phase 0 — Setup & Discovery · ~1 Woche

- [x] Repo-Skelett: `pyproject.toml` (uv), `src/loxone_voice/`, `tests/`, `docker-compose.yml`, `Dockerfile`
- [x] Linter & Format: `ruff`, `mypy --strict`, `pre-commit` mit `gitleaks`
- [x] Build-Pipeline `make build` (lint → typecheck → tests + 80 % Coverage-Gate)
- [x] Dependency-Audit (`make audit` mit `pip-audit`)
- [x] CI: GitHub Actions (lint + typecheck + test + audit)
- [x] Pydantic-Settings-Konfiguration mit Secrets-Handling
- [ ] **Miniserver-Erkundung**: über Browser `http://<miniserver>/data/LoxAPP3.json` ziehen, Struktur sichten *(braucht Zugang)*
- [ ] Liste der zu steuernden Räume / Geräte als Anforderungs-Snapshot festhalten *(braucht User-Input)*
- [ ] Test-User in Loxone Config anlegen mit reduzierten Rechten *(braucht User-Aktion)*

**Deliverable:** Lauffähiges `make build`, dokumentierter Geräte-Scope.

## Phase 1 — Loxone-Adapter · ~1,5 Wochen

- [ ] WebSocket-Client gegen Miniserver mit Token-Authentifizierung
- [ ] Keepalive + Reconnect-Logik
- [ ] Strukturdatei-Parser → Normalisiertes Domain-Modell (`Room`, `Control`, `Sensor`, `Scene`)
- [ ] Subscription auf State-Events, in-memory State-Cache
- [ ] CLI zum Testen: `loxctl rooms`, `loxctl set <name> on`, `loxctl state <name>`
- [ ] Unit-Tests mit gemocktem WebSocket; ein Integrationstest gegen echten Miniserver

**Deliverable:** Programmatisch jedes Loxone-Gerät schalten + abfragen können.

## Phase 2 — Intent-Engine mit Claude · ~1 Woche

- [ ] Anthropic SDK integriert (`claude-opus-4-7` als Default)
- [ ] Tool-Schemas definiert (siehe [claude-tools.md](claude-tools.md))
- [ ] System-Prompt-Generator: serialisiert Haus-Struktur kompakt
- [ ] Prompt-Caching für statische Anteile aktiviert
- [ ] Tool-Use-Loop mit Mehrfach-Iterationen, Fehler-Handling pro Tool
- [ ] Eval-Suite: 30+ Beispiel-Prompts mit erwarteten Tool-Calls

**Deliverable:** „Wohnzimmerlicht aus" über Python-Funktion ausführbar.

## Phase 3 — Telegram-Bot · ~1 Woche

- [ ] `aiogram 3` integriert, Long-Polling
- [ ] User-Whitelist via Env-Var
- [ ] `/start`, `/help`, `/status` Commands
- [ ] Text-Handler → Intent-Engine → Antwort
- [ ] Inline-Bestätigungs-Buttons für markierte Tools (`requires_confirmation`)
- [ ] Audit-Log JSONL

**Deliverable:** Erste echte Steuerung des Hauses per Telegram-Text-Chat.

## Phase 4 — Sprach-Nachrichten · ~3–5 Tage

- [ ] Voice-Message-Handler: Download `.ogg` aus Telegram
- [ ] Whisper-API-Integration (OpenAI), Fallback `whisper.cpp` lokal
- [ ] Spracherkennungs-Sprache konfigurierbar (DE default)
- [ ] Erkennungs-Konfidenz unter Schwelle → Rückfrage statt Ausführung

**Deliverable:** Sprach-Befehle im Telegram-Chat werden korrekt umgesetzt.

## Phase 5 — Statusabfragen & Reports · ~3–5 Tage

- [ ] Read-Tools für Claude: `get_state`, `query_sensors`, `list_open_windows`, `summarize_house`
- [ ] Aggregat-Tools (z. B. Schnittstellen für „alle Fenster", „Energieverbrauch heute")
- [ ] Verlaufs-Werte (falls Miniserver-Statistik-Server vorhanden)
- [ ] Beispiel-Reports: „Tagesabschluss", „Sind alle Türen zu?"

**Deliverable:** Claude kann jeden relevanten Zustand des Hauses berichten.

## Phase 6 — Szenen & Automationen anlegen · ~1,5 Wochen

- [ ] Eigenes Szenen-Modell (separat von Loxone-Config) in SQLite
- [ ] Tools: `create_scene`, `update_scene`, `delete_scene`, `run_scene`
- [ ] Optional: Zeit- / Bedingungs-Trigger → einfache Cron-artige Automationen
- [ ] „Was würde diese Szene tun?" — Dry-Run-Modus

**Deliverable:** User kann per Sprache neue Szenen erstellen und auslösen.

## Phase 7 — Alexa Custom Skill · ~2–3 Wochen

- [ ] Custom Skill in Alexa Developer Console
- [ ] AWS Lambda als Skill-Endpoint → HTTPS-Tunnel zum Heim-Gateway (Wireguard / Cloudflare Tunnel)
- [ ] OAuth Account-Linking (Telegram-User-ID ↔ Amazon-User-ID)
- [ ] Skill-Invocation: „Alexa, frag mein Haus, …"
- [ ] Reuse der bestehenden Intent-Engine — Alexa nur als zusätzlicher Channel

**Deliverable:** Identische Befehle funktionieren via Alexa und Telegram.

## Phase 8 — Härtung & Deployment · ~1 Woche

- [ ] Strukturierte Logs (`structlog`) + Log-Rotation
- [ ] Health- und Metrik-Endpunkte (`/healthz`, `/metrics`)
- [ ] Backup-Strategie für SQLite + Audit-Log
- [ ] Docker-Compose-Setup, Auto-Restart, Systemd-Unit als Alternative
- [ ] Setup-Dokumentation: „From-Zero auf Raspberry Pi"
- [ ] Penetration-Check der Whitelist + Rate-Limits

**Deliverable:** Produktionsreife Installation, dokumentiert.

## Zeitplan

| Phase | Dauer | Kumuliert |
| ----: | ----- | --------- |
| 0     | 1 W   | 1 W       |
| 1     | 1,5 W | 2,5 W     |
| 2     | 1 W   | 3,5 W     |
| 3     | 1 W   | 4,5 W     |
| 4     | 0,5 W | 5 W       |
| 5     | 0,5 W | 5,5 W     |
| 6     | 1,5 W | 7 W       |
| 7     | 2,5 W | 9,5 W     |
| 8     | 1 W   | 10,5 W    |

**MVP (Telegram-Steuerung per Text & Voice) ist nach Phase 4 erreicht — ca. 5 Wochen.**
