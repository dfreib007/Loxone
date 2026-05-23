# Loxone Voice Control

Steuerung eines **Loxone Miniservers** über natürliche Sprache —
zunächst via **Telegram-Bot**, später zusätzlich **Amazon Alexa**.
Die Intent-Erkennung übernimmt **Claude** (Anthropic API) mit Tool-Use.

> Status: **Planungsphase**. Dieses Repo enthält aktuell nur die
> Architektur- und Roadmap-Dokumente. Die Implementierung folgt
> in den in [docs/roadmap.md](docs/roadmap.md) beschriebenen Phasen.

## Ziel

Sätze wie *„Mach im Wohnzimmer das Licht auf 30 %, Rollos runter und
spiel etwas Musik"* werden in eine Sequenz von Loxone-Befehlen übersetzt
und ausgeführt — inklusive Rückmeldung im Chat.

Unterstützt werden:

- **Text-Nachrichten** im Messenger
- **Sprach-Nachrichten** (Telegram-Voice → Speech-to-Text → Claude)
- **Statusabfragen & Reports** („Sind alle Fenster zu?")
- **Szenen & Automationen anlegen** („Erstelle eine Kino-Szene …")

## Kern-Entscheidungen

| Bereich        | Entscheidung                                    |
| -------------- | ----------------------------------------------- |
| Messenger      | **Telegram** (Phase 1), Alexa (Phase 2)         |
| Hosting        | **Self-hosted im LAN** (Raspberry Pi / NAS / Docker) |
| Stack          | Python 3.12, FastAPI, aiogram 3, Anthropic SDK  |
| Loxone-API     | WebSocket + Token-Auth, Strukturdatei `LoxAPP3.json` |
| Speech-to-Text | OpenAI Whisper API (oder lokal `whisper.cpp`)    |

Mehr Detail in [docs/architecture.md](docs/architecture.md).

## Repository-Struktur (geplant)

```
.
├── README.md                  # diese Datei
├── docs/
│   ├── architecture.md        # Systemarchitektur & Komponenten
│   ├── roadmap.md             # Phasenplan
│   ├── claude-tools.md        # Tool-Katalog für die Intent-Engine
│   └── security.md            # Security-Konzept
├── src/
│   ├── loxone_voice/
│   │   ├── adapter/           # Loxone WebSocket-Client
│   │   ├── intent/            # Claude-Integration & Tools
│   │   ├── gateway/           # FastAPI + aiogram
│   │   └── config.py
│   └── tests/
├── docker-compose.yml
└── pyproject.toml
```

## Nächste Schritte

1. Plan reviewen → [docs/roadmap.md](docs/roadmap.md)
2. Phase 0 starten: Repo-Skelett, Dev-Setup, Miniserver-Discovery
3. Phase 1: Loxone-Adapter mit Token-Auth implementieren

Siehe [docs/roadmap.md](docs/roadmap.md) für die vollständige Phasenliste.
