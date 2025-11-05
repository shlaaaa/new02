"""Playwright helper to scrape GS Shop whisky products."""
from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from urllib.parse import parse_qs, urljoin, urlparse

from playwright.async_api import async_playwright, Locator, Page

LOGGER = logging.getLogger(__name__)


PRICE_PATTERN = re.compile(r"(\d+[\d,]*)")

CARD_SELECTOR = "a.prd-item"
NAME_SELECTOR = "dt.prd-name"
PRICE_SELECTOR = "dd.price-info .set-price strong"
IMAGE_SELECTOR = "div.prd-img img"
TOTAL_COUNT_SELECTOR = "#totalCnt"
ENTRY_DATA_SELECTOR = "#entry-data"
PAGINATION_LINK_SELECTOR = "nav.paging a[data-index=\"{index}\"]"


@dataclass
class Product:
    """Structured representation of a GS Shop product card."""

    name: str
    price: Optional[int]
    product_code: Optional[str]
    product_url: Optional[str]
    image_url: Optional[str]
    metadata: Dict[str, str] = field(default_factory=dict)


def _normalize_whitespace(value: Optional[str]) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value.strip())


async def _extract_card_data(card: Locator, *, base_url: str) -> Product:
    name = ""
    product_url = None
    price: Optional[int] = None
    product_code: Optional[str] = None
    image_url: Optional[str] = None

    name_locator = card.locator(NAME_SELECTOR).first
    if await name_locator.count() > 0:
        try:
            name = _normalize_whitespace(await name_locator.inner_text())
        except Exception as exc:  # pragma: no cover - defensive logging
            LOGGER.debug("Failed to read product name: %s", exc)

    price_locator = card.locator(PRICE_SELECTOR).first
    if await price_locator.count() > 0:
        try:
            price_text = _normalize_whitespace(await price_locator.inner_text())
            match = PRICE_PATTERN.search(price_text)
            if match:
                digits = match.group(1).replace(",", "")
                price = int(digits)
        except Exception as exc:  # pragma: no cover - defensive logging
            LOGGER.debug("Failed to parse price: %s", exc)

    href = await card.get_attribute("href")
    if href:
        absolute_href = urljoin(base_url, href)
        product_url = absolute_href
        parsed = urlparse(absolute_href)
        product_code = parse_qs(parsed.query).get("prdid", [None])[0]

    image_locator = card.locator(IMAGE_SELECTOR).first
    if await image_locator.count() > 0:
        try:
            src = await image_locator.get_attribute("src")
            if not src:
                src = await image_locator.get_attribute("data-src")
            if not src:
                srcset = await image_locator.get_attribute("srcset")
                if srcset:
                    src = srcset.split()[0]
            if src:
                if src.startswith("//"):
                    image_url = f"https:{src}"
                else:
                    image_url = urljoin(base_url, src)
        except Exception as exc:  # pragma: no cover - defensive logging
            LOGGER.debug("Failed to extract image: %s", exc)

    metadata: Dict[str, str] = {}
    if product_code:
        metadata["product_code"] = product_code
    try:
        raw_json = await card.get_attribute("data-info")
        if raw_json:
            metadata.update(json.loads(raw_json))
    except Exception:  # pragma: no cover - optional metadata
        pass

    if LOGGER.isEnabledFor(logging.DEBUG):
        LOGGER.debug(
            "Extracted card summary name=%r code=%s price=%s image=%s",
            name,
            product_code,
            price,
            image_url,
        )

    return Product(
        name=name,
        price=price,
        product_code=product_code,
        product_url=product_url,
        image_url=image_url,
        metadata=metadata,
    )


async def _read_total_count(page: Page) -> Optional[int]:
    try:
        raw_value = await page.eval_on_selector(TOTAL_COUNT_SELECTOR, "el => el?.value || el?.textContent || ''")
    except Exception:
        return None
    if not raw_value:
        return None
    raw_value = raw_value.strip()
    if raw_value.isdigit():
        return int(raw_value)
    if match := PRICE_PATTERN.search(raw_value):  # reuse pattern for digits
        try:
            return int(match.group(1).replace(",", ""))
        except ValueError:
            return None
    return None


async def _read_page_size(page: Page, fallback: int = 80) -> int:
    try:
        entry_data = await page.eval_on_selector(ENTRY_DATA_SELECTOR, "el => el?.textContent || ''")
    except Exception:
        entry_data = None
    if entry_data:
        try:
            data = json.loads(entry_data)
            page_size = int(data.get("param", {}).get("pageItemSize") or fallback)
            if page_size > 0:
                return page_size
        except Exception:
            LOGGER.debug("Failed to parse page size from entry-data")
    return fallback


async def _navigate_to_page(page: Page, index: int) -> None:
    selector = PAGINATION_LINK_SELECTOR.format(index=index)
    LOGGER.info("Navigating to page %d via selector %s", index, selector)
    link = page.locator(selector).first
    if await link.count() == 0:
        raise RuntimeError(f"Pagination link for page {index} not found")
    await link.click()
    await page.wait_for_load_state("networkidle")
    await page.wait_for_selector(CARD_SELECTOR, state="visible")


async def collect_products(url: str, min_items: int = 1000, headless: bool = True) -> List[Product]:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=headless)
        page = await browser.new_page()
        page.set_default_timeout(30_000)
        try:
            LOGGER.info("Navigating to %s", url)
            try:
                response = await page.goto(url, wait_until="domcontentloaded")
            except Exception as exc:
                LOGGER.exception("Navigation to %s failed: %s", url, exc)
                raise
            if response is not None:
                LOGGER.info(
                    "Initial navigation complete with status=%s and final_url=%s",
                    response.status,
                    response.url,
                )
            else:
                LOGGER.warning("page.goto returned no response object; content may be cached")
            await page.wait_for_selector(CARD_SELECTOR, timeout=30_000)
            await page.wait_for_load_state("networkidle")
            LOGGER.info("Page navigation complete; beginning product discovery (min_items=%d)", min_items)

            products: List[Product] = []
            seen_codes = set()
            skipped_duplicates = 0
            total_count = await _read_total_count(page)
            page_size = await _read_page_size(page)
            if total_count and page_size:
                last_page = max(1, math.ceil(total_count / page_size))
            else:
                last_page = 1
            LOGGER.info(
                "Pagination metadata total=%s page_size=%s last_page=%d",
                total_count,
                page_size,
                last_page,
            )

            for page_index in range(1, last_page + 1):
                if page_index > 1:
                    try:
                        await _navigate_to_page(page, page_index)
                    except Exception as exc:
                        LOGGER.warning("Failed to navigate to page %d: %s", page_index, exc)
                        break
                current_base_url = page.url
                cards = page.locator(CARD_SELECTOR)
                count = await cards.count()
                LOGGER.info(
                    "Processing page %d/%d with %d product cards", page_index, last_page, count
                )
                for idx in range(count):
                    card = cards.nth(idx)
                    product = await _extract_card_data(card, base_url=current_base_url)
                    if product.product_code and product.product_code in seen_codes:
                        LOGGER.debug("Skipping duplicate product_code %s", product.product_code)
                        skipped_duplicates += 1
                        continue
                    if product.product_code:
                        seen_codes.add(product.product_code)
                    if not product.name:
                        LOGGER.debug(
                            "Card %d on page %d produced empty name (code=%s)",
                            idx,
                            page_index,
                            product.product_code,
                        )
                        continue
                    products.append(product)
                    if len(products) >= min_items:
                        break
                if len(products) >= min_items:
                    LOGGER.info(
                        "Reached min_items threshold (%d) after page %d", min_items, page_index
                    )
                    break
            LOGGER.info(
                "Extraction summary: products=%d unique_codes=%d duplicates_skipped=%d",
                len(products),
                len(seen_codes),
                skipped_duplicates,
            )
            return products
        finally:
            await browser.close()


__all__ = ["collect_products", "Product"]
