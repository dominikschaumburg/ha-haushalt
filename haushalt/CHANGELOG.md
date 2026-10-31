# Changelog

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
