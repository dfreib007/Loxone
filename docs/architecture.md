# Architektur

## Übersicht

```
                       ┌─────────────────────────┐
                       │      Telegram Bot       │  ◀── Phase 1
                       │       (aiogram 3)       │
                       └───────────┬─────────────┘
                                   │
                       ┌───────────▼─────────────┐
                       │      Alexa Skill        │  ◀── Phase 7
                       │  (Lambda → HTTPS Hook)  │
                       └───────────┬─────────────┘
                                   │
            ┌──────────────────────▼──────────────────────┐
            │   Gateway / Orchestrator (FastAPI, async)   │
            │   - User-Auth & Whitelist                   │
            │   - Voice-Transkription (Whisper)           │
            │   - Konversations-State pro User            │
            │   - Bestätigungs-Workflow für „große" Aktionen │
            └──────────────────────┬──────────────────────┘
                                   │
            ┌──────────────────────▼──────────────────────┐
            │   Intent-Engine (Anthropic SDK)             │
            │   - Claude mit Tool-Use                     │
            │   - System-Prompt mit Home-Kontext          │
            │   - Multi-Turn / Tool-Call-Loop             │
            └──────────────────────┬──────────────────────┘
                                   │ Tool-Calls
            ┌──────────────────────▼──────────────────────┐
            │   Loxone-Adapter                            │
            │   - WebSocket-Client (Token-Auth, AES)      │
            │   - Strukturdatei-Parser (LoxAPP3.json)     │
            │   - Control-Repository (in-memory Cache)    │
            │   - Event-Stream (State-Updates)            │
            └──────────────────────┬──────────────────────┘
                                   │ WSS (LAN)
            ┌──────────────────────▼──────────────────────┐
            │          Loxone Miniserver                  │
            └─────────────────────────────────────────────┘
```

## Komponenten

### 1. Gateway / Orchestrator (`gateway/`)

- **FastAPI** als HTTP-Layer (für spätere Alexa-Webhooks und Health-Checks).
- **aiogram 3** für den Telegram-Bot (Long-Polling im LAN, kein Reverse-Proxy nötig).
- **User-Whitelist** auf Basis Telegram-User-IDs (Hardcoded in `.env`, später optional DB).
- **Sitzung pro User**: rollierendes Conversation-Window (z. B. letzte 10 Turns) für Multi-Turn-Dialoge.
- **Bestätigungs-Workflow** für destruktive Aktionen (z. B. „alle Rollos hoch", „Heizung aus") — Claude markiert solche Tools als `requires_confirmation`, der Gateway fragt vor Ausführung im Chat zurück.

### 2. Voice-Pipeline

- Telegram-Voice-Files (`.ogg`/Opus) werden direkt von der Bot-API geladen.
- Transkription via **OpenAI Whisper API** (Default) — optional lokal über `whisper.cpp` für maximalen Datenschutz.
- Transkripiertes Text-Prompt wird in dieselbe Intent-Engine geschickt wie Text-Eingaben.

### 3. Intent-Engine (`intent/`)

- **Anthropic Python SDK**, Modell `claude-opus-4-7` (oder `claude-sonnet-4-6` für günstigere Antworten).
- **System-Prompt** beinhaltet:
  - Beschreibung des Hauses (Räume, Geräte) — generiert aus `LoxAPP3.json`.
  - Verfügbare Szenen.
  - Sprach-/Stil-Vorgaben (knappe Bestätigungen, Deutsch).
- **Tool-Use-Loop**: Claude entscheidet, welche Loxone-Tools aufgerufen werden. Mehrfache Tool-Calls in einer User-Anfrage sind erlaubt (z. B. „Wohnzimmer-Atmosphäre" → 3 Tool-Calls).
- **Caching**: Der statische System-Prompt (Haus-Struktur) wird über Anthropic Prompt-Caching gecached → spart Token bei jedem Turn.
- Tool-Katalog im Detail: [claude-tools.md](claude-tools.md).

### 4. Loxone-Adapter (`adapter/`)

- **WebSocket-Client** nach offiziellem Schema (siehe `CommunicatingWithMiniserver.pdf`).
- **Token-Auth**: `jdev/sys/getPublicKey` → Session-Key-Negotiation → Token-Request mit HMAC-SHA1.
- **Keepalive** alle 4 Minuten (Server schließt nach 5 min Idle).
- **Strukturdatei** `LoxAPP3.json` wird beim Start geladen → in ein normalisiertes Modell überführt:
  ```
  Room   ─► Control (Switch / Dimmer / Jalousie / Sensor / Scene / …)
                  ├── states: {dict UUID → aktueller Wert}
                  └── commands: ['On', 'Off', 'Pulse', 'jumpToValue', …]
  ```
- **Event-Stream**: Status-Updates vom Miniserver werden in Cache geschrieben → `get_state` ist O(1) ohne Roundtrip.
- **Reconnect-Logik** mit Exponential-Backoff bei Netzwerk-Drop.

### 5. Persistenz

- **Phase 1–5**: Kein State außer Logs nötig — Conversation-Cache nur in-memory.
- **Phase 6 (Szenen anlegen)**: SQLite für User-defined Szenen / Automations-Metadaten.
- **Audit-Log** als Append-Only JSONL-Datei: wer/wann/was geschaltet wurde.

## Deployment

- **Docker-Compose** auf Raspberry Pi 4/5 oder NAS.
- Ein Container: `loxone-voice` (Python-App).
- Optional zweiter Container: `whisper-local` falls lokale Transkription.
- `.env` mit Secrets (Miniserver-Creds, Anthropic-Key, Telegram-Token).
- Restart-Policy `unless-stopped`, Healthcheck via `/healthz`.
- **Kein Port-Forwarding nötig**: Telegram nutzt Long-Polling outbound, der Miniserver bleibt im LAN.

## Datenflüsse (Sequenz)

**Text-Befehl „Mach das Wohnzimmerlicht aus":**

```
User → Telegram → aiogram → Gateway
                              │
                              ├─► Intent-Engine.run(user_msg, system_prompt+tools)
                              │     └─► Claude → tool_use: set_control(uuid=..., cmd="Off")
                              │           └─► Adapter.set_control(...) → WS → Miniserver
                              │     └─► Claude → final answer: „Wohnzimmerlicht ist aus."
                              ◄─ Gateway antwortet im Chat
```

**Sprach-Befehl:**

```
User → Telegram-Voice → aiogram (download .ogg)
                          └─► Whisper-Transkription
                                └─► identisch zu Text-Pfad
```

## Erweiterungen (Phase 7+)

- **Alexa Custom Skill** (AWS Lambda) → ruft denselben Gateway-Endpoint auf, mit OAuth-Account-Linking.
- **Web-UI** für Audit-Log und Szenen-Verwaltung.
- **Mehrsprachigkeit** über Claude (DE/EN automatisch).
- **Proaktive Benachrichtigungen** („Fenster Küche seit 2 h offen, soll ich schließen?").
