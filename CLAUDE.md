# Claude-Anweisungen für dieses Repository

Dieses Repo entwickelt einen **Loxone-Voice-Control-Service**, der ein
Smart Home steuert. Bugs oder Sicherheitslücken haben hier reale
physikalische Konsequenzen (offene Fenster, falsch geschaltete Heizung,
Verlust der Privatsphäre). **Halte deshalb in jeder Session und jedem
Commit die folgenden Standards ein.**

Vollständige Standards: [docs/coding-guidelines.md](docs/coding-guidelines.md),
[docs/security.md](docs/security.md).

## Coding-Standards (verbindlich)

- **Sprache:** Python 3.12+. Typ-Annotations sind Pflicht, `mypy --strict`
  muss grün sein. Keine `Any`-Casts ohne Begründung im Code-Kommentar.
- **Formatierung:** `ruff format` + `ruff check`. Kein Code wird ohne
  saubere Linter-Ausgabe committet.
- **Async-First:** I/O läuft `async`. Kein blockierendes `requests`,
  `time.sleep` etc. im Hot-Path — nutze `httpx`, `asyncio.sleep`.
- **Datenmodelle:** Pydantic v2 für alle externen Boundary-Daten
  (Telegram-Payloads, Loxone-Strukturdatei, Tool-Inputs/Outputs).
- **Fehlerbehandlung:** Kein `except:` und kein `except Exception:` ohne
  Re-raise oder strukturierten Log mit Kontext. Schlucke nie Fehler still.
- **Tests:** `pytest` + `pytest-asyncio`. **Jede Code-Änderung wird
  durch Unit-Tests begleitet — neuer Code, Bugfix oder Refactor.**
  Tests laufen im Build (`make build` / CI) und blocken den Commit/Merge,
  wenn sie failen. Coverage-Ziel ≥ 80 % auf `src/loxone_voice/`. Bei
  Bugfixes gilt TDD-Reihenfolge: erst der reproduzierende Test (rot),
  dann der Fix (grün).
- **Keine spekulativen Abstraktionen.** Drei ähnliche Stellen sind
  besser als eine verfrühte Generalisierung.
- **Kommentare nur, wenn das WARUM nicht aus dem Code hervorgeht.**
  Keine "added for issue #X"-Kommentare.

## Security-Standards (verbindlich)

- **Secrets niemals committen.** Nutze `.env`, das in `.gitignore` steht.
  Verifiziere bei jedem Commit, dass keine Keys/Tokens/Passwörter
  enthalten sind (`git diff --cached`).
- **Loxone-Credentials nur über Env-Vars.** Niemals in Code, Logs oder
  Fehlermeldungen.
- **Input-Validierung an jeder Boundary:** Telegram-Payloads,
  Tool-Argumente von Claude, Alexa-Webhooks → alle via Pydantic
  validieren. Niemals direkt in `set_control()` o. Ä. durchreichen.
- **Least Privilege:** App-User im Miniserver bekommt nur die nötigen
  Rechte. Dokumentiere das im Setup-Guide.
- **Prompt-Injection-Schutz:** Tool-Liste ist serverseitig fixiert;
  User-Text kann sie nicht erweitern. Destruktive Tools verlangen
  Bestätigung auch dann, wenn der User-Text das "überschreiben" will.
- **Audit-Log:** Jede Schreib-Aktion landet im Audit-Log mit
  User-ID, Tool, Argumenten, Ergebnis. Keine PII oder Voice-Daten dort.
- **Logging:** Strukturierte Logs (`structlog`). **Keine** Secrets,
  Roh-Tokens, Voice-Files, oder Telegram-Bot-Tokens in Logs.
- **Dependencies:** Nur aus PyPI mit gepinten Versionen
  (`uv lock`). Regelmäßiger `pip-audit` Lauf in CI. Keine
  ungeprüften Pakete von obskuren Quellen.
- **Krypto:** Nur etablierte Bibliotheken (`cryptography`,
  `pynacl`). Keine handgerollten Crypto-Funktionen.
- **OWASP ASVS Level 2** als Mindeststandard, [CWE Top 25][cwe]
  bei jedem Review im Hinterkopf.

[cwe]: https://cwe.mitre.org/top25/

## Was ich (Claude) in dieser Session automatisch tue

- Vor Code-Änderungen: lese die betroffenen Module vollständig.
- Vor jedem Commit: führe `ruff check`, `ruff format --check`,
  `mypy --strict src/`, `pytest` aus (sobald diese Tools eingerichtet sind).
- Wenn ich eine externe Abhängigkeit hinzufüge: prüfe Maintainer,
  letztes Release, Issue-Aktivität. Im Zweifel **frage nach**.
- Wenn eine Anforderung Security-relevant ist: ich dokumentiere die
  Entscheidung in `docs/security.md` und schreibe einen Test, der die
  Constraint verifiziert.
- Bei jedem Schreib-Tool für Claude (LLM-Tool-Use): ich überlege explizit,
  ob `requires_confirmation` gesetzt werden muss.

## Was ich **nicht** tue

- Nicht `--no-verify` bei Commits oder Pushes.
- Keine `git push --force` ohne ausdrückliche Genehmigung.
- Keine Secrets im Code, in Tests oder in Fixtures (nicht einmal
  als "Demo-Werte").
- Keine HTTP-Calls in Tests gegen echte Services ohne explizite
  Markierung (`@pytest.mark.integration`).
- Keine Internet-Erreichbarkeit des Miniservers aufbauen — alle
  externen Channels initiieren outbound.
- Kein "Quick Fix", der die Sicherheits-Pipeline umgeht. Wenn ein
  Linter / Test failt, fixe ich die Ursache, ich silence den Check nicht.

## Wenn ich unsicher bin

**Fragen statt raten.** Smart-Home-Steuerung in Production heißt: bei
Unklarheit (welcher Raum ist gemeint, sollen wirklich alle Fenster zu)
lieber eine Rückfrage als eine falsche Aktion. Gilt für mich genauso —
bei unklarer Anforderung frage ich nach, bevor ich Code schreibe.
