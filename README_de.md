[English](README.md) | [中文](README_zh.md) | Deutsch

# SimTradeData - Quantitativer Handelsdaten-Downloader

[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-red.svg)](LICENSE)
[![Version](https://img.shields.io/badge/Version-1.2.0-brightgreen.svg)](CHANGELOG.md)
[![DuckDB](https://img.shields.io/badge/Storage-DuckDB-FFF000?logo=duckdb&logoColor=black)](https://duckdb.org/)
[![Parquet](https://img.shields.io/badge/Export-Parquet-50ABF1)](https://parquet.apache.org/)
[![Code Style: Black](https://img.shields.io/badge/Code%20Style-Black-000000.svg)](https://github.com/psf/black)
[![uv](https://img.shields.io/badge/Packaging-uv-261230)](https://docs.astral.sh/uv/)

> **BaoStock + Mootdx + EastMoney + yfinance Multi-Quelle** | **Chinesische A-Aktien + US-Aktien** | **PTrade-kompatibel** | **DuckDB + Parquet-Speicherung**

**SimTradeData** ist ein effizientes Daten-Download-Tool für [SimTradeLab](https://github.com/kay-ou/SimTradeLab). Es unterstützt chinesische A-Aktien (BaoStock, Mootdx, EastMoney) und US-Aktien (yfinance) aus mehreren Datenquellen mit automatischer Orchestrierung der jeweiligen Stärken. Daten werden in DuckDB als Zwischenspeicher gespeichert und ins Parquet-Format exportiert, mit effizienten inkrementellen Updates und Abfragen.

---

<div align="center">

### Empfohlene Kombination: SimTradeData + SimTradeLab

**Voll PTrade-kompatibel | A-Aktien + US-Aktien | 20x+ Backtesting-Beschleunigung**

[![SimTradeLab](https://img.shields.io/badge/SimTradeLab-Quant_Backtesting-blue?style=for-the-badge)](https://github.com/kay-ou/SimTradeLab)

**Keine PTrade-Strategieänderungen nötig** | **Ultraschnelles lokales Backtesting** | **Kostenlose Lösung**

</div>

---

## Hauptmerkmale

### Effiziente Speicherarchitektur
- **DuckDB-Zwischenspeicher**: Hochleistungs-Spaltendatenbank mit SQL-Abfragen und inkrementellen Updates
- **Parquet-Exportformat**: Hoch komprimiert, plattformübergreifend kompatibel, ideal für Massendatenanalyse
- **Automatische inkrementelle Updates**: Erkennt vorhandene Daten intelligent, lädt nur neue Datensätze herunter

### Umfassende Datenabdeckung
- **Marktdaten**: OHLCV-Tagesbalken mit Limit-Up/Down-Preisen und Vortagesschluss
- **Bewertungskennzahlen**: KGV/KBV/KUV/KCV/Umschlagshäufigkeit/Gesamtaktien/Streubesitz
- **Finanzdaten**: 23 vierteljährliche Finanzkennzahlen + automatische TTM-Berechnung
- **Kapitalmaßnahmen**: Dividenden, Bonusaktien, Bezugsrechte (mit Vorwärts-Anpassungsfaktoren)
- **Metadaten**: Aktieninfo, Handelskalender, Indexbestandteile, ST/Aussetzungsstatus
- **US-Aktien**: 6.000+ US-Stammaktien, S&P 500 / NASDAQ-100 Indexbestandteile

### Datenqualitätssicherung
- **Auto-Validierung**: Datenintegritätsprüfung vor dem Schreiben
- **Export-Berechnung**: Limitpreise, TTM-Kennzahlen werden beim Export berechnet
- **Detailliertes Logging**: Umfassende Fehlerprotokolle und Warnungen

## Generierte Datenstruktur

```
data/
├── cn.duckdb                        # DuckDB-Datenbank - A-Aktien (Download-Quelle)
├── us.duckdb                        # DuckDB-Datenbank - US-Aktien (Download-Quelle)
└── export/                          # Exportierte Parquet-Dateien (nach Markt)
    ├── cn/                          # A-Aktien-Export
    │   ├── stocks/                  # Tagesbalken (eine Datei pro Aktie)
    │   │   ├── 000001.SZ.parquet
    │   │   └── 600519.SS.parquet
    │   ├── exrights/                # Kapitalmaßnahmen
    │   ├── fundamentals/            # Quartalsfinanzdaten (mit TTM)
    │   ├── valuation/               # Bewertungskennzahlen (täglich)
    │   ├── metadata/                # Metadaten
    │   └── manifest.json
    └── us/                          # US-Aktien-Export
        ├── stocks/
        │   ├── AAPL.US.parquet
        │   └── MSFT.US.parquet
        ├── exrights/
        ├── fundamentals/
        ├── valuation/
        ├── metadata/
        └── manifest.json
```

## Voraussetzungen

- **Python**: 3.10 oder höher
- **uv**: [Installationsanleitung](https://docs.astral.sh/uv/getting-started/installation/)
- **Netzwerk**: Zum Herunterladen von Daten erforderlich (für A-Aktien-Daten wird ein chinesisches Festlandnetzwerk empfohlen)

## Schnellstart

### Option 1: Vorgefertigte Daten herunterladen (Empfohlen)

Laden Sie die neuesten Daten von [Releases](https://github.com/kay-ou/SimTradeData/releases) herunter:

- **A-Aktien**: `data-cn-v*` Release → nach `data/cn/` entpacken
- **US-Aktien**: `data-us-v*` Release → nach `data/us/` entpacken

```bash
# A-Aktien
mkdir -p /path/to/SimTradeLab/data/cn
tar -xzf simtradelab-data-cn-*.tar.gz -C /path/to/SimTradeLab/data/cn/

# US-Aktien
mkdir -p /path/to/SimTradeLab/data/us
tar -xzf simtradelab-data-us-*.tar.gz -C /path/to/SimTradeLab/data/us/
```

### Option 2: Daten selbst herunterladen

#### 1. Abhängigkeiten installieren

```bash
git clone https://github.com/kay-ou/SimTradeData.git
cd SimTradeData
uv sync
```

#### 2. Daten herunterladen

**Empfohlen: Einheitlicher Download-Befehl**

Ein Befehl lädt alle Daten herunter, nutzt das TDX-Tagespaket für den schnellen OHLCV-Pfad und orchestriert danach Mootdx und BaoStock:

```bash
# Tägliche/produktive CN-Aktualisierung (empfohlen)
uv run python scripts/download.py --tdx-download --skip-mootdx-ohlcv

# Vollständige Fallback-Orchestrierung ohne TDX-Paket
uv run python scripts/download.py

# Bereits heruntergeladene TDX-ZIP-Datei verwenden
uv run python scripts/download.py --tdx-source data/downloads/hsjday.zip --skip-mootdx-ohlcv

# Datenstatus prüfen
uv run python scripts/download.py --status

# Finanzdaten überspringen (schneller)
uv run python scripts/download.py --tdx-download --skip-mootdx-ohlcv --skip-fundamentals
```

**Arbeitsteilung der Datenquellen**

| Datentyp | Quelle | Grund |
|----------|--------|-------|
| OHLCV-Marktdaten (taeglich/Vollhistorie) | TDX-Tagespaket | Am schnellsten, ~500MB Massenimport plus aktuelle Tagesdaten |
| OHLCV-Marktdaten (Fallback/Backfill) | Mootdx | Fallback pro Symbol, wenn das TDX-Paket nicht verfuegbar ist |
| Kapitalmaßnahmen (XDXR) | Mootdx | Vollständigere Daten |
| Massenfinanzdaten | Mootdx | Ein ZIP = alle Aktien |
| Bewertung KGV/KBV/KUV/Umschlag | BaoStock | Exklusive Daten |
| ST/Aussetzungsstatus | BaoStock | Exklusive Daten |
| Indexbestandteile | BaoStock | Exklusive Daten |
| Handelskalender | Mootdx | Liefert den Boersenkalender |
| Benchmark-Index | Mootdx | Liefert Benchmark-Zeitreihen |

**US-Aktiendaten (yfinance)**

```bash
# Vollständiger Download (6.000+ US-Aktien mit OHLCV + Finanzdaten + Bewertung + Metadaten)
uv run python scripts/download_us.py

# Bestimmte Symbole (für kleine Tests)
uv run python scripts/download_us.py --symbols AAPL,MSFT,GOOGL
```

#### 3. Nach Parquet exportieren

```bash
# A-Aktien exportieren → data/export/cn/
uv run python scripts/export_parquet.py

# US-Aktien exportieren → data/export/us/
uv run python scripts/export_parquet.py --market us

# Benutzerdefiniertes Ausgabeverzeichnis
uv run python scripts/export_parquet.py --market cn --output /custom/path
```

Der normale Export erstellt das Zielverzeichnis vollständig aus DuckDB neu; er führt die vorhandenen Parquet-Dateien nicht direkt zusammen.

#### 4. Daten veröffentlichen (Maintainer)

Beide Vertriebskanäle verwenden denselben DuckDB-/Parquet-Snapshot, veröffentlichen aber unterschiedliche Artefakte:

- **GitHub Releases** veröffentlicht ausschließlich vollständige Baseline-Archive.
- **Tencent COS** veröffentlicht eine vollständige Baseline und, wenn die vorherige COS-Version sicher fortgeschrieben werden kann, zusätzlich ein inkrementelles Archiv. Der Index `releases.json` macht beide für Clients auffindbar.

```bash
# A-Aktiendaten auf GitHub veröffentlichen
bash scripts/release_data.sh --market cn

# Die Release-Version stammt aus data/export/<market>/manifest.json

# Baseline und, wenn möglich, ein Delta auf Tencent COS veröffentlichen
COS_SECRET_ID=... COS_SECRET_KEY=... bash scripts/release_data.sh \
  --market cn --publish-targets cos --cos-bucket BUCKET --cos-region REGION
```

#### 5. In SimTradeLab verwenden

```bash
rsync -a data/export/cn/ /path/to/SimTradeLab/data/cn/
rsync -a data/export/us/ /path/to/SimTradeLab/data/us/
```

## Datenfeld-Referenz

### stocks/ - Tägliche Aktienbalken
| Feld | Beschreibung |
|------|-------------|
| date | Handelstag |
| open/high/low/close | OHLC-Preise |
| high_limit/low_limit | Limit-Up/Down-Preise (beim Export berechnet) |
| preclose | Vortagesschluss |
| volume | Handelsvolumen (Aktien) |
| money | Handelsbetrag (CNY) |

### valuation/ - Bewertungskennzahlen (Täglich)
| Feld | Beschreibung |
|------|-------------|
| pe_ttm/pb/ps_ttm/pcf | Bewertungskennzahlen |
| roe/roe_ttm/roa/roa_ttm | Rentabilitätskennzahlen |
| naps | Nettovermögen je Aktie |
| total_shares/a_floats | Gesamtaktien / Streubesitz |
| turnover_rate | Umschlagshäufigkeit |

### fundamentals/ - Finanzdaten (Vierteljährlich)
Enthält 23 Finanzkennzahlen und deren TTM-Versionen. Details siehe [PTRADE_PARQUET_FORMAT.md](docs/PTRADE_PARQUET_FORMAT.md).

## Datenquellen-Vergleich

| Merkmal | BaoStock | Mootdx API | EastMoney | TDX-Paket | yfinance (US) |
|---------|----------|------------|-----------|-----------|---------------|
| Markt | A-Aktien | A-Aktien | A-Aktien | A-Aktien | US-Aktien |
| Geschwindigkeit | Langsamer | Schnell | Schnell | Am schnellsten | Mittel |
| Bewertungsdaten | Ja | Nein | Nein | Nein | Ja (berechnet) |
| Finanzdaten | Ja (pro Aktie) | Ja (Massen-ZIP) | Nein | Nein | Ja (pro Aktie) |
| Geldfluss | Nein | Nein | Ja (exklusiv) | Nein | Nein |
| API-Schlüssel | Nicht erforderlich | Nicht erforderlich | Nicht erforderlich | N/A | Nicht erforderlich |

## Inkrementeller Aktualisierungsablauf

```bash
# 1. Nur neue Daten herunterladen; vorhandene Daten werden übersprungen
uv run python scripts/download.py --tdx-download --skip-mootdx-ohlcv

# 2. Vollständigen Parquet-Snapshot exportieren (ersetzt das Ausgabeverzeichnis)
uv run python scripts/export_parquet.py

# 3. Nur für Maintainer: eigenständiges Delta-Paket exportieren
uv run python scripts/export_parquet.py --delta --base-version 2026-06-20 --target-version 2026-06-22
```

Der Delta-Export erzeugt ein separates Paket mit den geänderten Zeilen der Symboltabellen und einem Manifest für das angegebene Versionsfenster.

Endnutzer führen diesen Delta-Befehl nicht aus und wählen keinen Aktualisierungsmodus. SimTradeDeskX fordert bei SimTradeAPI eine automatische Aktualisierung an: Zuerst wird ein konfigurierter dynamischer Delta-Endpunkt verwendet, danach eine statische COS-Delta-Kette. Bei der Erstinstallation, einer unvollständigen Delta-Kette oder einem fehlgeschlagenen Delta wird automatisch auf eine vollständige Baseline zurückgegriffen. Die API wendet die gesamte Delta-Kette zunächst auf einen Snapshot an und veröffentlicht ihn anschließend durch atomaren Verzeichnistausch. Bei einem Fehler bleiben die bisherigen nutzbaren Daten erhalten. GitHub Releases dienen als vollständiger Baseline-Fallback; COS unterstützt die indexierte inkrementelle Verteilung.

## Tests

```bash
uv run pytest tests/ -v
```

## Verwandte Links

- **SimTradeLab**: https://github.com/kay-ou/SimTradeLab
- **BaoStock**: http://baostock.com/
- **Mootdx**: https://github.com/mootdx/mootdx
- **yfinance**: https://github.com/ranaroussi/yfinance

## 💖 Sponsoring

Wenn dieses Projekt Ihnen hilft, erwägen Sie eine Spende!

| WeChat Pay | Alipay |
|:---:|:---:|
| <img src="docs/sponsor/WechatPay.png?raw=true" alt="WeChat Pay" width="200"> | <img src="docs/sponsor/AliPay.png?raw=true" alt="Alipay" width="200"> |

## Lizenz

Dieses Projekt ist unter AGPL-3.0 lizenziert. Siehe [LICENSE](LICENSE).

---

**Status**: Produktionsbereit | **Version**: v1.2.0 | **Letzte Aktualisierung**: 2026-03-13
