"""CLI entry point for scraping GS Shop whisky listings."""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional

from .scraper import collect_products, Product


LOGGER = logging.getLogger(__name__)


@dataclass
class OutputConfig:
    output_dir: Path
    prefix: str
    emit_csv: bool
    emit_json: bool


DEFAULT_URL = "https://www.gsshop.com/shop/wine/cate.gs?msectid=1548240"


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--min-items",
        type=int,
        default=1000,
        help="Minimum number of product cards to capture before exporting (default: 1000)",
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_URL,
        help="Target listing URL (default: %(default)s)",
    )
    parser.add_argument(
        "--output-dir",
        default="data",
        help="Directory where output files will be written (default: %(default)s)",
    )
    parser.add_argument(
        "--prefix",
        default="gsshop_whisky",
        help="File prefix for generated artifacts (default: %(default)s)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON output (default: enabled unless --no-json is set)",
    )
    parser.add_argument(
        "--csv",
        action="store_true",
        help="Emit CSV output (default: enabled unless --no-csv is set)",
    )
    parser.add_argument(
        "--no-json",
        action="store_true",
        help="Disable JSON output",
    )
    parser.add_argument(
        "--no-csv",
        action="store_true",
        help="Disable CSV output",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Force headless browser mode (default)",
    )
    parser.add_argument(
        "--no-headless",
        dest="headless",
        action="store_false",
        help="Disable headless browser mode for debugging",
    )
    parser.set_defaults(headless=True)
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging level (default: %(default)s)",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def ensure_output_config(args: argparse.Namespace) -> OutputConfig:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    emit_csv = True
    emit_json = True
    if args.csv:
        emit_csv = True
    if args.json:
        emit_json = True
    if args.no_csv:
        emit_csv = False
    if args.no_json:
        emit_json = False
    if not (emit_csv or emit_json):
        raise ValueError("At least one output format (CSV/JSON) must be enabled")
    return OutputConfig(output_dir=output_dir, prefix=args.prefix, emit_csv=emit_csv, emit_json=emit_json)


def write_outputs(products: List[Product], config: OutputConfig) -> List[Path]:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    written: List[Path] = []
    if config.emit_json:
        json_path = config.output_dir / f"{config.prefix}_{timestamp}.json"
        with json_path.open("w", encoding="utf-8") as fh:
            json.dump([asdict(p) for p in products], fh, ensure_ascii=False, indent=2)
        written.append(json_path)
    if config.emit_csv:
        csv_path = config.output_dir / f"{config.prefix}_{timestamp}.csv"
        with csv_path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(
                fh,
                fieldnames=["name", "price", "product_code", "product_url", "image_url", "metadata"],
            )
            writer.writeheader()
            for product in products:
                row = asdict(product)
                row["metadata"] = json.dumps(row.get("metadata", {}), ensure_ascii=False)
                writer.writerow(row)
        written.append(csv_path)
    return written


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level), format="%(asctime)s %(levelname)s %(message)s")
    output_config = ensure_output_config(args)

    LOGGER.info("Starting scrape of %s", args.url)

    products = asyncio.run(collect_products(args.url, min_items=args.min_items, headless=args.headless))

    LOGGER.info("Fetched %d products", len(products))

    written_paths = write_outputs(products, output_config)
    for path in written_paths:
        LOGGER.info("Wrote %s", path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
