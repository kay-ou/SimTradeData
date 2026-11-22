[English](README.md) | [中文](README_zh.md) | Deutsch

# SimTradeData

Lokale Backtesting-Daten für [SimTradeLab](https://github.com/kay-ou/SimTradeLab): chinesische Aktien, ETFs und US-Aktien. Speicherung in DuckDB, Export als Parquet.

## Weboberfläche starten

Python 3.10+, [uv](https://docs.astral.sh/uv/getting-started/installation/) und Node.js 20+ mit npm installieren. Im Projektverzeichnis:

```bash
npm run ui
```

Die im Terminal angezeigte Adresse öffnen (ab **http://127.0.0.1:8765**, belegte Ports werden automatisch übersprungen). Optional einen festen Port wählen:

```bash
npm run ui -- --port 8767
```

Mit **更新回测数据** werden Aktien und ETFs gemeinsam ergänzt. Gültige vorhandene Daten werden übersprungen. Fortschritt, Abbruch und Wiederholung fehlgeschlagener Einheiten sind sichtbar. Aktien- und ETF-Statistiken werden getrennt angezeigt; Quartalsberichte und ETF-PCF stehen unter **其他数据**.

Standarddatenbank: `data/cn.duckdb`; anderes Verzeichnis mit `--data-dir /path/to/data`. Der Datenbestand bestimmt den Aktualisierungsbedarf; kein gespeicherter Downloadverlauf. `npm run ui` installiert Abhängigkeiten und baut bei Bedarf automatisch. Der erste freie Port ab 8765 wird gewählt; die Adresse steht im Terminal. Chinesisch, Englisch und Deutsch sind oben umschaltbar; die erste Auswahl folgt dem Browser; die manuelle Auswahl wird gespeichert. US-Daten und Exporte erfolgen über die CLI.

## Fertige Daten oder CLI

[Releases](https://github.com/kay-ou/SimTradeData/releases) bieten `data-cn-v*` und `data-us-v*`. Parquet-Dateien nach SimTradeLab `data/cn/` bzw. `data/us/` entpacken. Diese Pakete sind keine aktualisierbaren DuckDB-Quelldatenbanken.

Für die CLI Python und uv installieren, `uv sync` ausführen, dann:

```bash
uv run python scripts/download.py
uv run python scripts/export_parquet.py
```

Standardexport: `data/export/cn/`. Weboberfläche und CLI nicht gleichzeitig in dieselbe Datenbank schreiben lassen.

## Dokumentation und Grenzen

| Thema | Anleitung |
| --- | --- |
| Oberfläche und Datenpflege | [Web-Anleitung](docs/WEB_DATA_ADMIN.md) |
| US-Daten und Parquet-Export | [CLI-Anleitung](docs/CLI_GUIDE.md) |
| Datenabdeckung und PTrade-Grenzen | [Datenabdeckung](docs/PTRADE_DATA_COVERAGE.md) |
| CI, Überwachung und Wiederherstellung | [Betrieb](docs/OPERATIONS.md) |
| Formate, Finanzfelder und PCF | [Dokumentationsübersicht](docs/README.md) |

Detailanleitungen werden auf Chinesisch gepflegt; die Betriebsdokumentation enthält Englisch. 223 öffentliche Finanzfelder sind zugeordnet. Vollständige PTrade-Gleichwertigkeit, historische Wertpapierlisten und lückenlose PCF-Historien sind nicht garantiert. ETF-Kursbacktests benötigen keine PCF. Erfolgreiche Stichproben beweisen keine vollständige Historie.

[Änderungen](CHANGELOG.md) · [Lizenz: AGPL-3.0](LICENSE)
