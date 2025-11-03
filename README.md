# GS Shop Whisky Scraper

This repository contains an automated scraper that collects up to 1,000 whisky (양주) listings from [GS Shop](https://www.gsshop.com/shop/wine/cate.gs?msectid=1548240). The scraper uses Playwright to render the dynamic product grid, extracts product metadata, and exports both JSON and CSV artifacts. A GitHub Actions workflow is included to run the scraper on demand or on a schedule and publish the artifacts.

## Requirements

- Python 3.10+
- [Playwright](https://playwright.dev/python/)

Install dependencies locally:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install --with-deps chromium
```

## Usage

Run the scraper via the CLI entry point. By default it will collect at least 1,000 products and store CSV/JSON outputs in the `data/` directory.

```bash
python -m scraper.main --min-items 1000 --output-dir data
```

Useful options:

- `--url`: Override the target GS Shop listing URL.
- `--min-items`: Number of product cards to gather (default: 1000).
- `--no-headless`: Run Chromium with a visible window for debugging.
- `--no-csv` / `--no-json`: Control which file formats are emitted.

Outputs are timestamped using UTC and contain the following fields:

| Field | Description |
| --- | --- |
| `name` | Product title text. |
| `price` | Price in KRW (integer). |
| `product_code` | GS Shop product identifier when available. |
| `product_url` | Link to the product detail page. |
| `image_url` | Primary thumbnail URL. |
| `metadata` | Additional data parsed from card attributes when available. |

## GitHub Actions

The workflow defined in [`.github/workflows/scrape.yml`](.github/workflows/scrape.yml) performs the following steps:

1. Installs Python and Playwright.
2. Runs the scraper in headless mode.
3. Uploads the generated CSV/JSON as workflow artifacts for download.

You can trigger the workflow manually via the **Run workflow** button or rely on the scheduled cron job.

## Development

The parsing helpers are structured to make it easy to add unit tests with saved HTML fixtures. The scraper logs progress and retries scrolling / “load more” interactions to capture dynamically loaded products.
