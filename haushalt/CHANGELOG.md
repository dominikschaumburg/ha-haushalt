# Changelog

## 1.8.3

- Statistik → Verlauf: Linien geglättet (gleitender Mittelwert 1:2:1 mit den Nachbarwochen bzw. -monaten, weich gezeichnet ohne Überschwingen). Die echten Werte stehen als blasse Punkte im Diagramm und im Tooltip.

## 1.8.2

- Kategorie „Pflanzen & Ordnung“ aufgeteilt: Startset und Vorlagen nutzen „Pflanzen“, Aufräumen und Ausmisten gehören zu „Putzen“. Bestehende Installationen bleiben unverändert.

## 1.8.1

- Nach einer Aktion beim Abhaken (z. B. Verbrauchszähler zurücksetzen) werden die Sensoren nach 10 Sekunden erneut gelesen und die Ansicht aktualisiert – vorher blieb die Aufgabe bis zum nächsten Laden fällig.
- Sensor-Regel „fällt auf die Grenze“ zeigt bei Gleichstand das Teil mit dem kleinsten Restwert.

## 1.8.0

- Einfacher einrichten: „Erste Schritte“ auf der Aufgabenseite (Personen, Benachrichtigungen, Vorlagen), bis man sie mit „Fertig“ schließt.
- Benachrichtigungsdienste werden automatisch zugeordnet – über die Tracker der HA-Person oder ein Gerät der Companion-App, das nach der Person heißt („iPhone von Sarah“). Ein bewusst geleertes Feld bleibt leer.
- Benachrichtigungen standardmäßig nur, wenn jemand zu Hause ist (`zone.home`); „Nur wenn an“ akzeptiert auch Zahlen größer 0.
- Saugroboter-Vorlage „leeren“ nutzt einen Behälter-Sensor der Integration, falls vorhanden.

## 1.7.0

- Aufgaben aus Vorlagen (Einstellungen → Aufgaben → „Aus Vorlagen …“): Katalog mit über 80 typischen Aufgaben samt Rhythmus und Aufwand.
- Passend zu den Bereichen in Home Assistant: Vorschläge je Raum (Küche, Bad, Balkon, Schlafzimmer …), die dem Raum gleich zugeordnet werden.
- Passend zu den Geräten in Home Assistant: Saugroboter (Wartung mit Verbrauchszählern und Reset-Knöpfen, Behälter), Spülmaschine, Waschmaschine und Trockner (über „wartet/fertig“-Schalter), Filter mit Restlaufzeit, ein gemeinsames „Batterien wechseln“ für alle Batteriegeräte, Bodenfeuchte, Rauchmelder und Kaffeemaschine, Wasserkocher oder Boiler an Mess-Steckdosen. Vorhandene Aufgaben werden erkannt und auf Wunsch um Sensor-Regel, Aktion oder Raum ergänzt.
- Aufgaben haben einen Raum: Filter in der Aufgabenliste und Auswertung „Räume“ in der Statistik.
- Sensor-Regeln verstehen an/aus-Zustände (an = 1).

## 1.6.0

- Lernender Rhythmus: Aus den Abständen zwischen den Erledigungen (Median der letzten 10, ab 3 Abständen seit der letzten Änderung des Rhythmus, mehrere Erledigungen am selben Tag zählen einmal) lernt die App, wie oft eine Aufgabe wirklich anfällt – auch wenn sie vor der Fälligkeit erledigt wird. Bei Aufgaben mit festem Rhythmus ersetzt der gelernte Wert den Standard (⌀), ohne Rhythmus erscheint er als Vorschlag im Editor. Sensor-Regeln gelten unverändert zusätzlich.

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
