# Changelog

## 1.5.0

- Neue Einstellung unter Benachrichtigungen: Alle anderen bekommen eine Nachricht, wenn jemand eine Aufgabe erledigt (nicht für Einträge, die mehr als 6 Stunden nachgetragen werden). Standardmäßig aus.

## 1.4.0

- Fälligkeit aus Sensoren: Aufgaben können zusätzlich zum Rhythmus auf Home-Assistant-Entitäten hören – „Wert fällt auf die Grenze“ (z. B. Restlaufzeit von Saugroboter-Filtern), „Wert erreicht die Grenze“ (z. B. Zähler) oder „Zuwachs seit dem Abhaken“ (z. B. kWh eines Energiezählers). Fällig ist, was zuerst greift.
- Aktion beim Abhaken: ein Home-Assistant-Dienst mit optionalen Ziel-Entitäten, z. B. `button.press` zum Zurücksetzen von Verbrauchsteilen.
- Benachrichtigungen: einmal am Tag zur eingestellten Uhrzeit für fällige Aufgaben mit 🔔, pro Aufgabe für ausgewählte Personen, optional nur wenn eine Entität an bzw. aus ist. „Erledigt ✅“ in der Nachricht hakt die Aufgabe ab; offene Nachrichten verschwinden, sobald jemand die Aufgabe erledigt.
- `sensor.haushalt_faellig` berücksichtigt Sensor-Regeln; das Attribut `details` enthält jetzt den Status als Text.

## 1.3.1

- Aufgabenansicht: Kategorien und Aufgaben sind nach Häufigkeit sortiert, das am häufigsten Erledigte steht oben. „Jetzt dran“ und „Demnächst“ bleiben nach Dringlichkeit sortiert.

## 1.3.0

- Lernender Aufwand: Beim Abhaken wird der Median der letzten 10 Einträge vorgeschlagen (ab 3 Einträgen seit der letzten Änderung des Standards). Der Aufgaben-Editor zeigt den gelernten Wert und kann ihn als Standard übernehmen.
- Symbol- und Namensfeld im Aufgaben-Editor richtig dargestellt.

## 1.2.0

- Danke sagen: Emoji-Reaktionen auf Aufgaben, die andere erledigt haben, mit eigener Karte auf der Aufgabenseite, Anzeige im Verlauf, in der Wochenübersicht und in der Statistik.
- Neues Ereignis `haushalt_reaktion` für Benachrichtigungen.
- Reaktionen sind Teil von Export und Import.

## 1.1.1

- Seitenleisten-Eintrag auch für Benutzer ohne Admin-Rechte.

## 1.1.0

- Import eines JSON-Exports unter Einstellungen → Daten (ersetzt den gesamten Bestand, z. B. für Umzüge). Der Export enthält jetzt auch das Änderungsprotokoll.

## 1.0.1

- Aufgaben, die noch nie erledigt wurden, gelten nicht mehr als fällig; der Rhythmus beginnt mit dem ersten Abhaken.
- Startbefehl über `with-contenv`, damit Token und Zeitzone ankommen.

## 1.0.0

- Erste Version: Aufgaben mit Rhythmus und Aufwand, Abhaken pro angemeldeter Person, Verlauf mit Bearbeiten, Löschen und Wiederherstellen, Statistik mit Trends, Heatmaps und Aufteilung, Sensoren und Ereignis für Home Assistant.
