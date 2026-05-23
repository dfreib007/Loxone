# Claude Tool-Katalog

Die Intent-Engine nutzt Claude mit **Tool-Use**. Claude bekommt diese
Tools als JSON-Schemas; jede vom Modell ausgelöste Tool-Invocation wird
vom Adapter ausgeführt und das Ergebnis als nächstes Turn-Input zurück
ans Modell geschickt.

Markierung `[!]` = `requires_confirmation`: Der Gateway holt vor Ausführung
eine explizite User-Bestätigung im Chat ein.

## Lese-Tools (read-only)

### `list_rooms()`
Listet alle Räume aus der Strukturdatei.
**Returns:** `[{id, name, category}]`

### `list_controls(room_id?: str, type?: str)`
Listet steuerbare Geräte, optional gefiltert nach Raum oder Typ
(`Switch`, `LightController`, `Jalousie`, `IRoomController`, `Sensor`, …).
**Returns:** `[{id, name, type, room_id, current_state}]`

### `get_state(control_id: str)`
Aktueller Zustand eines Geräts inkl. aller Unter-States.
**Returns:** `{control_id, states: {key: value, …}, last_updated}`

### `query_sensors(kind?: 'temperature' | 'humidity' | 'illuminance' | 'co2' | 'window' | 'door', room_id?: str)`
Convenience-Wrapper für Sensor-Werte über mehrere Räume.
**Returns:** `[{room, sensor_name, value, unit}]`

### `summarize_house()`
Kompakte Gesamt-Übersicht (für „Wie ist die Lage zu Hause?").
**Returns:** `{open_windows: [...], lights_on: [...], avg_temp_by_room: [...], alarms: [...]}`

### `list_scenes()`
Listet Loxone-Szenen + im System angelegte User-Szenen.
**Returns:** `[{id, name, description, source: 'loxone' | 'user'}]`

## Schreib-Tools (Aktionen)

### `set_control(control_id: str, command: str, value?: number)`
Schaltet ein einzelnes Gerät. `command` muss zu den im Strukturmodell
deklarierten Kommandos passen (z. B. `On`, `Off`, `Pulse`, `jumpToValue`).
**Returns:** `{ok: bool, new_state?}`

### `set_room_state(room_id: str, action: 'all_off' | 'all_on' | 'dim', value?: number)` `[!]`
Alle Lichter / Geräte eines Raums.

### `set_blinds(target: 'room' | 'house', room_id?: str, position: 'up' | 'down' | number)` `[!]` (für `house`)
Steuert Rollos / Jalousien.

### `set_thermostat(control_id: str, target_temperature: number, mode?: 'auto' | 'heat' | 'cool' | 'off')`
Heizungs-/Klima-Steuerung.

### `run_scene(scene_id: str)`
Auslösen einer existierenden Szene.

### `house_global(action: 'sleep_mode' | 'leave_home' | 'arrive_home' | 'all_off')` `[!]`
Globale Modus-Schalter. Ruft i. d. R. eine vordefinierte Loxone-Szene auf.

## Konfigurations-Tools (Phase 6)

### `create_scene(name: str, description: str, actions: [{control_id, command, value?}])`
Legt eine neue benutzerdefinierte Szene an (in SQLite, **nicht** in
Loxone-Config — wir editieren nicht den Miniserver, sondern führen Aktionen
sequenziell aus).

### `update_scene(scene_id: str, ...)` / `delete_scene(scene_id: str)` `[!]`

### `create_automation(trigger: {...}, actions: [...])` `[!]`
Einfache zeit- oder zustandsbasierte Automation.
Beispiel-Trigger: `{type: 'cron', expr: '0 22 * * *'}` oder
`{type: 'state_change', control_id, condition}`.

## System-Tools

### `confirm_destructive(plan_id: str, user_decision: 'yes' | 'no')`
Wird intern vom Gateway aufgerufen, nicht vom Modell — Modell sieht
das Resultat als Tool-Result.

### `now()` / `weather(location?)`
Kontext-Helpers, falls Claude Tageszeit / Wetter braucht.

## Schema-Beispiel (kompakt)

```python
{
    "name": "set_control",
    "description": "Schaltet ein einzelnes Loxone-Gerät. control_id muss aus list_controls stammen.",
    "input_schema": {
        "type": "object",
        "properties": {
            "control_id": {"type": "string"},
            "command": {"type": "string", "description": "z.B. 'On', 'Off', 'Pulse', 'jumpToValue'"},
            "value": {"type": "number", "description": "Optional, z.B. Helligkeit 0-100"}
        },
        "required": ["control_id", "command"]
    }
}
```

## Tool-Use-Strategie

- **Prompt-Caching**: Der System-Prompt mit Haus-Struktur ist statisch
  pro Session — über `cache_control: ephemeral` cachen, spart ~80 %
  Input-Tokens.
- **Parallel-Tool-Calls** sind erlaubt — Claude kann pro Turn mehrere
  Tools gleichzeitig auslösen (z. B. Wohnzimmer-Atmosphäre = Licht + Rollos + Musik).
- **Hard limit**: Max 6 Tool-Calls pro User-Turn, sonst Abort + Rückfrage.
- **Idempotenz**: Schreib-Tools sollen mit gleichem Input dasselbe Ergebnis
  liefern (Schalter „On" bei bereits an = OK, nicht Fehler).
