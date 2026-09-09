# Amazing Stock Data

Optional external history integration for [Amazing Stock Card](https://github.com/raunosr/ha-amazing-stock). Adds Avanza market history, including **5 years, 10 years and MAX**, without waiting for Home Assistant to record those years.

Existing price sensors remain the quote source. This integration creates no sensors, accesses no brokerage account and needs no API key. The card and this integration are installed separately through HACS. Home Assistant 2024.8 or newer is required.

## Install

1. HACS → Custom repositories → add `https://github.com/raunosr/ha-amazing-stock-data`, category **Integration**.
2. Download **Amazing Stock Data** and restart Home Assistant.
3. Settings → Devices & services → Add integration → **Amazing Stock Data** → Submit.
4. Update Amazing Stock Card to **v0.2.0 or newer**.
5. In the card editor select **History source → Avanza**. No `configuration.yaml` edits are needed.

The card recognizes IDs in existing `sensor.avanza_stock_3873` style names. For a renamed sensor or another source, open that row's settings and enter the Avanza instrument ID for the **same listing and currency**. It is the numeric ID in the instrument's Avanza page URL. The ID is not the ticker. An ID entered explicitly takes priority over the sensor name.

Optional dashboard card YAML:

```yaml
type: custom:amazing-stock-card
history_provider: avanza
show_header: false
show_chart: true
show_sparklines: true
default_period: five_years
entities:
  - entity: sensor.avanza_stock_3873
  - entity: sensor.my_other_price_sensor
    history_id: "3873"
  - entity: sensor.local_fund
    history_provider: home_assistant
grid_options:
  columns: 12
  rows: 8
```

Replace example sensors with your own. Use a name or symbol override in the card when desired; provider metadata supplies missing symbols and listing details automatically after history loads.

## Data and limitations

- Avanza's **public, unofficial web endpoints** supply the historical OHLC closes. There is no paid API licence or API key in this implementation, but availability, rate limits and terms are controlled by Avanza and may change. It is intended for a personal dashboard, not redistribution of market data.
- USA stocks are supported, including NASDAQ Microsoft and NYSE Nokia ADR. Stocks, ETFs and ETPs have been checked. Other Avanza instrument IDs work only if the same public chart and listing endpoints supply them; unsupported instruments show an error and can use HA history instead.
- 5 years uses daily closes; 10 years and MAX use weekly closes. Shorter periods use the resolution selected by Avanza. The card labels the actual resolution. Weekly timestamps identify source bars, not an exchange tick time.
- MAX means **all history returned by Avanza for that listing**, not necessarily the company's full lifetime. For example, Microsoft currently starts in 1998 on this endpoint. Younger listings have less data. The time axis uses actual returned sample dates and never fills missing earlier years.
- Chart change is first-to-last of the returned historical closes. It may differ from the current sensor price and its daily/weekly percentage, which remain sourced from the original sensor. No current quote is appended to the historical series.
- Corporate-action/dividend adjustment is not specified by the endpoint. Values are displayed as supplied, with no claim of total return or independent split adjustment. Currencies are not converted; the card refuses to plot a known history/sensor currency mismatch.
- A displayed quote may still be delayed. Adding this integration does not make existing Avanza sensors real time.
- External-source failure is visible. There is no silent recorder substitution for a 5/10-year chart. Choose Home Assistant explicitly to use recorder data.

## How it works

The frontend calls `amazing_stock_data/history` through Home Assistant's authenticated WebSocket. The integration checks the user's read permission for the referenced sensor and routes the request to an allowlisted provider. No browser-to-Avanza requests, CORS workaround, login cookies or API keys are used.

Requests are shared across cards and devices: 5 minutes for intraday/week/month, 1 hour for year/5y/10y/MAX and 24 hours for listing metadata. Cache size is capped at 256 entries, with three simultaneous upstream HTTP calls and a bounded pending queue. HTTP requests time out after 20 seconds; error retries and HTTP 429 responses back off. Cache data stays in memory and is cleared on restart. HA recorder retention is unaffected.

The response contract is provider-neutral: `provider`, `source`, `period`, `instrument` (ID, name, symbol, currency, market, kind), actual `start`/`end` and `{time,value}` points in Unix milliseconds, `resolution`, `partial`, `fetched_at`, `adjustment` and chart `change`. Adding another provider belongs behind this transport boundary; original sensor integrations remain independent.

## Development

```sh
python -m venv .venv
# Activate the environment for your shell.
pip install -r requirements-test.txt
python -m unittest discover -s tests -v
```

Tests use synthetic responses and a local HTTP server. They cover periods, invalid values, gaps, request validation, cache bounds and expiry, coalescing, cancellation, rate limits, response-size limits and unload. CI also validates HACS metadata, Home Assistant integration structure with Hassfest and code with CodeQL. A live HA installation verifies the actual WebSocket and config flow.

Release from a passing protected `main` commit. Bump the manifest version and tag a GitHub release. HACS downloads `custom_components/amazing_stock_data` from that release. Never include tokens, real HA configuration or market datasets in the repository.

## Suomeksi

Asenna HACSista tämä repositorio tyypillä **Integraatio**, käynnistä HA uudelleen ja lisää **Amazing Stock Data** kohdasta Asetukset → Laitteet ja palvelut. Valitse kortin editorista historian lähteeksi **Avanza**. Nykyisten `sensor.avanza_stock_ID`-sensorien tunnukset löytyvät automaattisesti. Uudelleennimetylle sensorille syötä saman pörssilistauksen Avanza-tunnus rivin asetuksiin.

Pitkä historia tulee Avanzalta ja nykyinen kurssi edelleen vanhasta sensoristasi. 5 vuoden kuvaaja käyttää päivien, 10 vuoden ja MAX-kuvaaja viikkojen päätöskursseja. MAX näyttää kaiken Avanzalta saatavan historian, joka voi olla kohteen koko elinkaarta lyhyempi. Datan viive ei muutu. Integraatio ei tarvitse tiliä tai API-avainta eikä luo uusia sensoreita.

[MIT license](LICENSE) · [Security](SECURITY.md)
