# Coding-Guidelines

Verbindliche Standards für **jede** Code-Änderung in diesem Repo.
Kurzform für Claude liegt in [../CLAUDE.md](../CLAUDE.md). Security-Aspekte
ergänzend in [security.md](security.md).

## 1. Tests sind Pflicht — keine Ausnahme

**Jede Code-Änderung wird durch Unit-Tests begleitet.** Das gilt für
neuen Code, Bugfixes und Refactorings. Tests laufen automatisch im
Build und blocken den Commit/Merge, wenn sie failen.

Konkret:

- **Neuer Code:** mindestens ein Unit-Test, der das Happy-Path-Verhalten
  abdeckt; bei nicht-trivialer Logik zusätzlich Edge-Cases (leere
  Eingabe, Grenz­werte, ungültige Argumente).
- **Bugfixes:** **zuerst** ein Test, der den Bug reproduziert (rot),
  **dann** der Fix (grün). Verhindert Regressionen.
- **Refactorings:** existierende Tests müssen grün bleiben; neue Tests
  nur, wenn Verhalten verändert oder Lücken aufgedeckt werden.
- **Tools für Claude (`intent/tools/`):** jedes neue Tool bekommt
  Tests für Schema-Validierung, Happy Path, Fehler-Pfad und (falls
  zutreffend) `requires_confirmation`-Verhalten.
- **Loxone-Adapter:** WebSocket-Verhalten wird gegen einen Mock-Server
  getestet (`tests/fixtures/loxone_mock.py`). Echte Miniserver-Tests
  unter `@pytest.mark.integration`, nicht im Standard-Build.

### Build-Pipeline (CI + lokales `make build`)

```
ruff format --check          # Format
ruff check                   # Lint
mypy --strict src/           # Type-Check
pytest --cov=src/loxone_voice --cov-fail-under=80   # Tests + Coverage-Gate
pip-audit                    # Dependency-Vulnerabilities
```

Failt **eine** Stufe, schlägt der gesamte Build fehl. Es gibt keinen
"Quick-Skip" und kein `--no-verify`. Wenn ein Test flakey ist, wird
die Ursache gefixt — nicht der Test ausgeschlossen.

### Lokal vor jedem Commit

`make build` (oder `pre-commit run --all-files`) wird vor jedem Commit
ausgeführt. Pre-commit-Hook ist verpflichtend installiert (`pre-commit install`
nach `git clone`).

## 2. Sprache, Stil, Struktur

- **Python 3.12+**. Match-Statements, Type-Parameter-Syntax und
  `typing.override` sind ok.
- **Type Hints überall.** `mypy --strict`. `Any` nur mit Inline-
  Kommentar, der begründet warum.
- **`ruff format`** ist die Single Source of Truth fürs Formatieren.
  Keine manuellen Style-Diskussionen.
- **Naming:** `snake_case` für Funktionen/Variablen, `PascalCase` für
  Klassen, `UPPER_SNAKE` für Konstanten. Englisch. Keine Abkürzungen
  ausser sehr etablierten (`url`, `id`, `db`).
- **Module ≤ 400 Zeilen** als Richtwert. Längere Module aufteilen.
- **Public-API jedes Pakets** geht durch `__init__.py`. Interne Module
  starten mit `_` oder leben in `_internal/`.

## 3. Async-First

- Alle I/O-Operationen sind `async`. `requests`, `time.sleep`, blockierende
  Locks sind in `src/` verboten.
- HTTP-Client: `httpx.AsyncClient`. WebSocket: `websockets`.
- Concurrency-Helpers: `asyncio.gather`, `asyncio.TaskGroup`. Tasks
  immer mit `name=` benennen.
- Cancellation wird respektiert — `try/finally`-Cleanup statt
  `BaseException`-Catches.

## 4. Datenmodelle & Validierung

- **Pydantic v2** für alle Boundary-Modelle:
  - Telegram-Inbound-Payloads
  - Anthropic-Tool-Inputs (vom Modell zurückkommend)
  - Loxone-Strukturdatei
  - User-Konfiguration (`pydantic-settings`)
- **Dataclasses** (`@dataclass(slots=True, frozen=True)`) für rein
  interne Domänen-Objekte, wenn keine Serialisierung nötig.
- **Keine `dict[str, Any]`** durch die Codebase reichen. Wenn ein
  externes Format unstrukturiert ist, am Eingang validieren.

## 5. Fehlerbehandlung

- **Niemals `except:`** und kein nacktes `except Exception:` ohne
  spezifischen Grund + Log.
- Fehler werden mit Kontext geloggt (`logger.error("...", extra={...})`)
  und entweder behoben oder weitergeworfen — niemals stillschweigend
  verschluckt.
- **Custom Exceptions** pro Subsystem: `LoxoneAdapterError`,
  `IntentEngineError`, `GatewayAuthError`. Sie erben von `Exception`,
  nicht von `BaseException`.
- **Retries** nur mit klarem Backoff + Maximum-Versuchen, dokumentiert
  warum retry-fähig.

## 6. Logging

- **`structlog`** als einzige Logging-Bibliothek.
- Felder: `event`, `user_id` (gehasht, falls extern), `tool`,
  `control_id`, `duration_ms`. Keine PII, keine Voice-Bytes, keine Secrets.
- Log-Level:
  - `DEBUG` lokale Entwicklung, nie in Production-Default.
  - `INFO` jede ausgeführte Aktion (zusätzlich zu Audit-Log).
  - `WARNING` recovery-fähige Probleme.
  - `ERROR` fehlgeschlagene Operationen.
- **Audit-Log** läuft separat (JSONL append-only) — siehe [security.md](security.md).

## 7. Dependencies

- **`uv`** als Package-Manager. `uv lock` wird committet.
- **Pinning:** Lockfile ist die Wahrheit.
- **Audit:** `pip-audit` im CI. Bei jedem Dependabot/Renovate-PR muss
  ein Mensch das Changelog der Dependency anschauen.
- **Neue Dependency hinzufügen** = überlegen ob die Funktionalität
  in <50 Zeilen selbst geschrieben werden kann. Im Zweifel: nicht hinzufügen.
- **Keine Pakete** mit < 1000 Downloads/Monat oder ohne Wartung
  (letzter Commit > 18 Monate) ohne explizite Begründung.

## 8. Project-Layout

```
src/loxone_voice/
├── __init__.py
├── config.py                  # pydantic-settings
├── adapter/                   # Loxone WS-Client
│   ├── __init__.py
│   ├── client.py
│   ├── auth.py
│   ├── structure.py
│   └── models.py
├── intent/                    # Claude-Integration
│   ├── __init__.py
│   ├── engine.py
│   ├── system_prompt.py
│   └── tools/                 # ein File pro Tool
│       ├── __init__.py
│       ├── set_control.py
│       ├── get_state.py
│       └── ...
├── gateway/                   # Channels
│   ├── __init__.py
│   ├── telegram_bot.py
│   ├── alexa.py               # Phase 7
│   └── confirm.py
└── audit.py

tests/
├── unit/                      # mirror der src-Struktur
│   ├── adapter/
│   ├── intent/
│   └── gateway/
├── integration/               # @pytest.mark.integration
└── fixtures/                  # gemeinsame Test-Daten + Mocks
```

## 9. Code-Review-Checkliste

Vor jedem Push (Self-Review oder PR):

- [ ] Tests vorhanden, alle grün
- [ ] Coverage ≥ 80 % auf neuen Files
- [ ] `mypy --strict` ohne Fehler
- [ ] `ruff check` & `ruff format --check` clean
- [ ] Keine Secrets, keine Print-Statements, keine `TODO ohne Issue`
- [ ] Bei neuem Schreib-Tool: `requires_confirmation` bewusst gesetzt?
- [ ] Bei neuer Dependency: Audit-Check + Begründung im Commit
- [ ] Commit-Message erklärt das WARUM, nicht nur das WAS

## 10. Performance & Resource Use

- Long-Running-Tasks: explizit cancelable.
- Speicher: keine ungebundenen Caches (verwende `functools.lru_cache(maxsize=…)`).
- Auf Raspberry Pi muss der Service mit ≤ 200 MB RAM laufen — bei jeder
  Architektur-Entscheidung mitdenken.
- Anthropic-API: Prompt-Caching nutzen, Token-Verbrauch pro
  Conversation loggen (für Cost-Monitoring).

## 11. Dokumentation

- **Docstrings** auf Public-Functions: kurz, ein Satz was und ein Satz
  wofür. Keine Mehrzeilen-Romane.
- **`docs/`** für Architektur-Entscheidungen und User-Facing-Setup-Guides.
- **`CHANGELOG.md`** (ab Phase 1) — `Keep a Changelog`-Format.

## 12. Was ausdrücklich verboten ist

- `eval`, `exec`, `pickle` auf externen Daten.
- `subprocess.shell=True` ohne extreme Begründung.
- HTTP ohne TLS in irgendeinem Code-Pfad.
- Logging von Secrets oder Raw-Tokens (Maskierung via
  structlog-Processor erzwungen).
- Committen von `*.env`, `*.key`, `*.pem`, `audit.jsonl`, `data/`.
- `git commit --no-verify`, `git push --force` (außer User fordert es
  explizit für ein Feature-Branch an).
