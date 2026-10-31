# Haushalt

## Bedienung

**Aufgaben**: Der Kreis rechts hakt eine Aufgabe für dich ab (die in Home Assistant angemeldete Person). Ein Tipp auf die Aufgabe öffnet das Erfassen-Fenster: Dort kannst du andere oder mehrere Personen wählen, den Zeitpunkt nachtragen (z. B. „Gestern“), den Aufwand anpassen und eine Notiz hinterlassen. Bei mehreren Personen wird der Aufwand gleichmäßig geteilt.

Der farbige Ring um das Symbol zeigt, wie viel vom Rhythmus abgelaufen ist: grün im Plan, gelb bald bzw. heute fällig, rot überfällig. Aufgaben ohne Rhythmus haben keinen Ring.

**Verlauf**: Alle Einträge nach Tagen. Antippen zum Bearbeiten, Papierkorb zum Löschen. Mit „Gelöschte zeigen“ lassen sich gelöschte Einträge wiederherstellen.

**Statistik**: Zeitraum (4 Wochen, 3 Monate, 12 Monate, Gesamt) und Maß (Zeit oder Anzahl) oben wählen. Hover oder Tippen auf Diagramme zeigt Details. „Wer macht was?“ lässt sich je Kategorie aufklappen.

**Einstellungen**: Personen (Name, Farbe, aktiv), Aufgaben (Symbol, Kategorie, Rhythmus, Aufwand), Kategorien und Export als JSON oder CSV. Aufgaben mit Einträgen werden beim Entfernen archiviert, damit die Statistik erhalten bleibt.

## Konfiguration

| Option | Standard | Bedeutung |
|---|---|---|
| `sensors` | `true` | Sensoren in Home Assistant anlegen (alle 5 Minuten und nach jeder Änderung aktualisiert) |
| `log_level` | `info` | Ausführlichkeit des Logs |

## Sensoren und Ereignisse

- `sensor.haushalt_faellig`: Anzahl fälliger und überfälliger Aufgaben; Attribut `aufgaben` enthält die Namen.
- `sensor.haushalt_<name>_7_tage`: Minuten pro Person in den letzten 7 Tagen; Attribute `aufgaben` und `anteil` (in %).
- Ereignis `haushalt_erledigt` mit `aufgabe`, `personen`, `minuten`, `zeitpunkt` bei jedem neuen Eintrag.

Beispiel: abends erinnern, wenn etwas überfällig ist.

```yaml
automation:
  - alias: Haushalt-Erinnerung
    triggers:
      - trigger: time
        at: "19:00:00"
    conditions:
      - condition: numeric_state
        entity_id: sensor.haushalt_faellig
        above: 0
    actions:
      - action: notify.notify
        data:
          message: "Heute fällig: {{ state_attr('sensor.haushalt_faellig', 'aufgaben') | join(', ') }}"
```

## Aus Automationen abhaken

Home Assistant kann Einträge über einen `rest_command` anlegen, etwa per NFC-Tag am Mülleimer. Aufgabe und Person werden per Namen angegeben. Den Hostnamen der App findest du unter **Einstellungen → Apps → Haushalt** (Feld „Hostname“): `local-haushalt` bei lokaler Installation, sonst z. B. `a1b2c3d4-haushalt`.

```yaml
rest_command:
  haushalt_erledigt:
    url: http://a1b2c3d4-haushalt:8099/api/completions
    method: POST
    content_type: application/json
    payload: '{"task": "{{ aufgabe }}", "person": "{{ person }}"}'
```

```yaml
action: rest_command.haushalt_erledigt
data:
  aufgabe: Restmüll runterbringen
  person: Dominik
```

Die App nimmt Anfragen nur über Ingress und von Home Assistant selbst an.

## Daten

Alle Daten liegen in `/data/haushalt.db` (SQLite) und sind Teil der Home-Assistant-Backups. Einträge werden nie endgültig gelöscht; jede Änderung wird zusätzlich in der Tabelle `audit` protokolliert.
