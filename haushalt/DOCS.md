# Haushalt

## Bedienung

**Aufgaben**: Der Kreis rechts hakt eine Aufgabe für dich ab (die in Home Assistant angemeldete Person). Ein Tipp auf die Aufgabe öffnet das Erfassen-Fenster: Dort kannst du andere oder mehrere Personen wählen, den Zeitpunkt nachtragen (z. B. „Gestern“), den Aufwand anpassen und eine Notiz hinterlassen. Bei mehreren Personen wird der Aufwand gleichmäßig geteilt.

Der farbige Ring um das Symbol zeigt, wie viel vom Rhythmus abgelaufen ist: grün im Plan, gelb bald bzw. heute fällig, rot überfällig. Aufgaben ohne Rhythmus haben keinen Ring.

**Danke sagen**: Was die anderen in den letzten drei Tagen erledigt haben und worauf du noch nicht reagiert hast, steht oben auf der Aufgabenseite. Ein Tipp auf ein Emoji schickt ein Danke. Im Verlauf geht das über das Herz neben dem Eintrag; ein Tipp auf eine eigene Reaktion nimmt sie zurück. Erhaltene Danke stehen in der Wochenübersicht und in der Statistik.

**Verlauf**: Alle Einträge nach Tagen. Antippen zum Bearbeiten, Papierkorb zum Löschen. Mit „Gelöschte zeigen“ lassen sich gelöschte Einträge wiederherstellen.

**Statistik**: Zeitraum (4 Wochen, 3 Monate, 12 Monate, Gesamt) und Maß (Zeit oder Anzahl) oben wählen. Hover oder Tippen auf Diagramme zeigt Details. „Wer macht was?“ lässt sich je Kategorie aufklappen.

**Aufwand**: Jede Aufgabe hat einen Standardaufwand. Sobald es seit der letzten Änderung des Standards mindestens 3 Einträge gibt, schlägt die App stattdessen den Median der letzten 10 Einträge vor – auch beim schnellen Abhaken. Wer den Aufwand beim Abhaken korrigiert, bringt der App also bei, wie lange eine Aufgabe wirklich dauert. Der Median reagiert erst, wenn mehrere Einträge abweichen, einzelne Ausreißer verschieben ihn nicht. Im Aufgaben-Editor steht der gelernte Wert neben dem Standard und lässt sich übernehmen; ein neuer Standard startet das Lernen neu.

**Rhythmus**: Auch der Rhythmus lernt mit – aus den Abständen zwischen den Erledigungen, egal ob eine Aufgabe fällig war. Ab 3 Abständen seit der letzten Änderung des Rhythmus gilt der Median der letzten 10 (mehrere Erledigungen am selben Tag zählen einmal). Bei Aufgaben mit festem Rhythmus ersetzt er den Standard und ist mit ⌀ markiert; bei Aufgaben ohne Rhythmus schlägt der Editor ihn nur vor („Als Rhythmus übernehmen“). Ein neuer Rhythmus startet das Lernen neu.

**Fälligkeit aus Sensoren**: Im Aufgaben-Editor unter „Home Assistant“ kann eine Aufgabe zusätzlich zum Rhythmus auf Entitäten hören. Fällig ist sie, sobald Rhythmus oder Sensor-Regel greift – die Sensor-Regel gilt auch, wenn die Aufgabe noch nie abgehakt wurde.

| Regel | Fällig, wenn | Beispiel |
|---|---|---|
| Wert fällt auf die Grenze | eine Entität ≤ Grenze ist | Restlaufzeit von Filter und Bürsten ≤ 0 h |
| Wert erreicht die Grenze | eine Entität ≥ Grenze ist | 3 Saugvorgänge seit dem Leeren |
| Zuwachs seit dem Abhaken | der Wert seit dem letzten Abhaken um die Grenze gestiegen ist | 1 kWh am Wasserkocher-Zähler |

Bei mehreren Entitäten reicht eine. Der Ring zeigt, wie nah die Regel an der Grenze ist (bei „fällt auf“ gemessen am höchsten bisher gesehenen Wert).

**Aktion beim Abhaken**: Ein Dienst wie `button.press` oder `counter.reset` mit optionalen Ziel-Entitäten läuft bei jedem neuen Eintrag – so setzt das Abhaken etwa die Verbrauchszähler des Saugroboters zurück.

**Benachrichtigungen**: Jede Person braucht den Dienst ihrer Companion-App (z. B. `notify.mobile_app_iphone`). Die App ordnet ihn selbst zu, wenn das Handy als Tracker in der HA-Person steckt oder nach der Person benannt ist; sonst unter Einstellungen → Benachrichtigungen auswählen. Dort gibt es außerdem die Uhrzeit sowie „Nur wenn an“ (Standard `zone.home`, also nur wenn jemand zu Hause ist; auch z. B. `binary_sensor.jemand_zu_hause`) und „Nicht wenn an“ (z. B. `calendar.urlaub`). Aufgaben mit eingeschalteter Benachrichtigung (🔔) melden sich dann einmal am Tag, solange sie fällig sind, bei den in der Aufgabe gewählten Personen. „Erledigt ✅“ in der Nachricht hakt die Aufgabe für diese Person ab und führt die Aktion aus; erledigt jemand die Aufgabe, verschwinden die Nachrichten auf den anderen Geräten. Mit „Alle anderen benachrichtigen, wenn jemand eine Aufgabe erledigt“ bekommen alle übrigen Personen bei jedem neuen Eintrag eine kurze Nachricht (nicht bei Einträgen, die mehr als 6 Stunden nachgetragen werden).

**Vorlagen**: Unter Einstellungen → Aufgaben → „Aus Vorlagen …“ gibt es drei Bereiche zum Anhaken:

- *Eure Geräte*: aus Home Assistant erkannte Geräte mit fertiger Sensor-Regel und Aktion – Saugroboter (Wartung über die Restlaufzeit-Sensoren, Abhaken drückt die Reset-Knöpfe; die Knöpfe sind in der Integration oft deaktiviert und müssen erst aktiviert werden), Spülmaschine, Wasch­maschine und Trockner (über Schalter wie `input_boolean.geschirr_wartet`), Filter mit Restlaufzeit, Batteriegeräte (ohne Handys, Tablets und Akkugeräte), Bodenfeuchte-Sensoren, Rauchmelder sowie Kaffeemaschine, Wasserkocher und Boiler an Steckdosen mit Strommessung.
- *Eure Räume*: Vorschläge passend zu den Bereichen in Home Assistant; sie bekommen den Raum gleich zugeordnet.
- *Alle Vorlagen*: der ganze Katalog nach Kategorien.

Was es schon gibt, ist ausgegraut. Passt eine Vorlage zu einer vorhandenen Aufgabe, ergänzt sie diese um Sensor-Regel, Aktion oder Raum, statt eine zweite anzulegen.

**Räume**: Jede Aufgabe kann einen Bereich aus Home Assistant bekommen. Dann lässt sich die Aufgabenliste nach Raum filtern, und die Statistik zeigt, wie viel Zeit in welchem Raum steckt.

**Einstellungen**: Personen (Name, Farbe, aktiv), Aufgaben (Symbol, Kategorie, Rhythmus, Aufwand, Home Assistant), Kategorien, Benachrichtigungen und Export als JSON oder CSV. Aufgaben mit Einträgen werden beim Entfernen archiviert, damit die Statistik erhalten bleibt.

## Konfiguration

| Option | Standard | Bedeutung |
|---|---|---|
| `sensors` | `true` | Sensoren in Home Assistant anlegen (alle 5 Minuten und nach jeder Änderung aktualisiert) |
| `log_level` | `info` | Ausführlichkeit des Logs |

## Sensoren und Ereignisse

- `sensor.haushalt_faellig`: Anzahl fälliger und überfälliger Aufgaben (Rhythmus und Sensor-Regeln); Attribut `aufgaben` enthält die Namen, `details` Name und Status.
- `sensor.haushalt_<name>_7_tage`: Minuten pro Person in den letzten 7 Tagen; Attribute `aufgaben` und `anteil` (in %).
- Ereignis `haushalt_erledigt` mit `aufgabe`, `personen`, `minuten`, `zeitpunkt` bei jedem neuen Eintrag.

- Ereignis `haushalt_reaktion` mit `von`, `emoji`, `aufgabe`, `fuer` (Namen), `ha_user_ids` und `zeitpunkt`, wenn jemand Danke sagt.

Beispiel: Push-Nachricht, wenn sich jemand bedankt (Benachrichtigungsdienst anpassen).

```yaml
automation:
  - alias: Haushalt-Danke an Sarah
    triggers:
      - trigger: event
        event_type: haushalt_reaktion
    conditions:
      - condition: template
        value_template: "{{ 'Sarah' in trigger.event.data.fuer }}"
    actions:
      - action: notify.mobile_app_iphone_von_sarah
        data:
          message: "{{ trigger.event.data.von }} sagt {{ trigger.event.data.emoji }} für „{{ trigger.event.data.aufgabe }}“"
```

Beispiel: eigene Sammel-Erinnerung per Automation (alternativ zu den eingebauten Benachrichtigungen).

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
