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

- [x] Strukturdatei-Parser → Normalisiertes Domain-Modell (`Room`, `Control`, `Category`, `StructureFile`)
- [x] In-memory State-Cache (`StateStore`) mit getrennten Value- und Text-Kanälen
- [x] Token-Auth-Crypto-Primitives (RSA-Wrapping, AES-CBC, SHA1/SHA256 HMAC, Token-Modell mit redacted repr)
- [x] WebSocket-Binär-Header-Parser (8-Byte-Header, Microsoft-GUID-Layout, Event-Parser)
- [x] HTTP-Discovery (`fetch_public_key` mit injizierbarem httpx-Client)
- [x] WebSocket-Client (`LoxoneClient`) mit vollem Handshake (Keyexchange → getkey2 → gettoken → enablebinstatusupdate)
- [x] Event-Loop dispatcht Value-/Text-Events in den `StateStore`
- [x] Keepalive-Task alle 240 s
- [x] Bidirektionale Slash-Boundary-Korrelation für Command-Responses, Orphan-Buffer für Out-of-Order
- [ ] CLI zum Testen: `loxctl rooms`, `loxctl set <name> on`, `loxctl state <name>` *(verschoben, kommt mit echter Miniserver-Anbindung)*
- [ ] Reconnect mit Exponential-Backoff *(später, eigene Schicht)*
- [ ] Integrationstest gegen echten Miniserver *(braucht Zugang)*

**Deliverable:** Programmatisch jedes Loxone-Gerät schalten + abfragen können. **Status: erreicht im Mock; reale Verifikation steht aus.**

## Phase 2 — Intent-Engine mit Claude · ~1 Woche

- [x] Tool-Framework (`Tool`, `ToolError`, Pydantic-Args-Validation, Anthropic-Schema-Export)
- [x] Loxone-Tools: `list_rooms`, `list_controls` (filterbar), `get_state`, `set_control` (mit `requires_confirmation`)
- [x] System-Prompt-Generator aus `StructureFile`, mit Favoriten-Sortierung und Truncation
- [x] Prompt-Injection-Schutz im Behavioural-Prefix (User-Text als Daten)
- [x] `IntentEngine` mit Tool-Use-Loop, Anthropic-Client als Protocol injizierbar
- [x] Prompt-Caching auf statischer Haus-Struktur
- [x] Fehlerbehandlung: unknown tool, validation error, handler crash → Tool-Result mit `is_error=true`
- [x] Iteration-Cap + Conversation-Buffer-Trimming
- [ ] Eval-Suite: 30+ Beispiel-Prompts mit erwarteten Tool-Calls *(später, Phase 2c)*

**Deliverable:** „Wohnzimmerlicht aus" via Python-Funktion ausführbar. **Status: erreicht (gegen Mock-Anthropic + Mock-Loxone).**

## Phase 3 — Telegram-Bot · ~1 Woche

- [x] User-Whitelist (`UserWhitelist`) mit `WhitelistViolation`
- [x] Channel-agnostisches Gateway (`Gateway.handle_text`) mit Audit-Integration und Rate-Limiting
- [x] Per-User-IntentEngine-Cache + `reset_user` für Konversations-Cleanup
- [x] aiogram 3 Adapter (`TelegramGateway`) mit `/start`, `/help`, `/reset`, Text, Voice
- [x] `build_dispatcher` registriert Handler in Prioritätsreihenfolge
- [x] Audit-Log mit gehashter User-ID
- [ ] Inline-Bestätigungs-Buttons für `requires_confirmation`-Tools *(Phase 3c)*
- [ ] Echtes End-to-End-Smoke gegen Telegram *(braucht Bot-Token)*

**Deliverable:** Erste echte Steuerung des Hauses per Telegram-Text-Chat. **Status: Code-vollständig; reale Aktivierung braucht Bot-Token + Miniserver.**

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
