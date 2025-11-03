"""Playwright helper to scrape GS Shop whisky products."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from playwright.async_api import async_playwright, Locator, Page

LOGGER = logging.getLogger(__name__)


PRICE_PATTERN = re.compile(r"(\d+[\d,]*)")


@dataclass
class Product:
    """Structured representation of a GS Shop product card."""

    name: str
    price: Optional[int]
    product_code: Optional[str]
    product_url: Optional[str]
    image_url: Optional[str]
    metadata: Dict[str, str] = field(default_factory=dict)


async def _extract_card_data(card: Locator) -> Product:
    name_selectors = [
        "a.goodsTxt",  # desktop selector
        "a.prd-name",
        "div.info a",
        "a",
    ]
    name = None
    product_url = None
    for selector in name_selectors:
        locator = card.locator(selector).first
        if await locator.count() == 0:
            continue
        try:
            text = (await locator.inner_text()).strip()
        except Exception as exc:  # pragma: no cover - defensive logging
            LOGGER.debug("Failed to read text for %s: %s", selector, exc)
            continue
        href = await locator.get_attribute("href")
        if text:
            name = text
        if href:
            product_url = href
        if name:
            break
    price = await _extract_price(card)
    product_code = await _extract_product_code(card)
    image_url = await _extract_image(card)
    metadata = {}
    if product_code:
        metadata["product_code"] = product_code
    try:
        raw_json = await card.get_attribute("data-info")
        if raw_json:
            metadata.update(json.loads(raw_json))
    except Exception:  # pragma: no cover - optional metadata
        pass
    return Product(
        name=name or "",
        price=price,
        product_code=product_code,
        product_url=product_url,
        image_url=image_url,
        metadata=metadata,
    )


async def _extract_price(card: Locator) -> Optional[int]:
    price_selectors = [
        "span.price",
        "span.price strong",
        "em.prc",
        "span.selling",
        "span.goods_prc",
    ]
    for selector in price_selectors:
        locator = card.locator(selector).first
        if await locator.count() == 0:
            continue
        try:
            text = (await locator.inner_text()).strip()
        except Exception:
            continue
        if match := PRICE_PATTERN.search(text):
            digits = match.group(1).replace(",", "")
            try:
                return int(digits)
            except ValueError:
                LOGGER.debug("Failed to parse price from %s", digits)
    return None


async def _extract_product_code(card: Locator) -> Optional[str]:
    attr_candidates = ["data-goodsno", "data-product-id", "data-code", "data-goods-code"]
    for attr in attr_candidates:
        value = await card.get_attribute(attr)
        if value:
            return value
    try:
        dataset = await card.evaluate("(el) => el.dataset")
        if dataset:
            for key in ("goodsno", "productId", "code"):
                value = dataset.get(key)
                if value:
                    return str(value)
    except Exception:
        pass
    return None


async def _extract_image(card: Locator) -> Optional[str]:
    img_selectors = ["img", "img.prd-img", "div.thumb img"]
    for selector in img_selectors:
        locator = card.locator(selector).first
        if await locator.count() == 0:
            continue
        src = await locator.get_attribute("src")
        if src:
            return src
    return None


CARD_SELECTORS = [
    "[data-info]",
    "li[data-info]",
    "div[data-info]",
    "li.prod-item",
    "li[class*='prd']",
    "div.product-item",
    "li",
]


async def _get_product_cards(page: Page) -> List[Locator]:
    """Return locators for product cards discovered on the page."""

    LOGGER.debug("Scanning for product cards using selectors: %s", ", ".join(CARD_SELECTORS))
    for selector in CARD_SELECTORS:
        cards = page.locator(selector)
        count = await cards.count()
        if count == 0:
            LOGGER.debug("Selector %s located no elements", selector)
            continue
        LOGGER.debug("Located %d cards with selector %s", count, selector)
        filtered: List[Locator] = []
        for idx in range(count):
            card = cards.nth(idx)
            try:
                data_info = await card.get_attribute("data-info")
            except Exception:
                data_info = None
            if data_info or selector == "li" or selector == "div.product-item":
                filtered.append(card)
        if filtered:
            LOGGER.info(
                "Selector %s yielded %d filtered product cards (raw count: %d)",
                selector,
                len(filtered),
                count,
            )
            return filtered
    LOGGER.info("No candidate selectors matched; returning empty product list")
    return []


async def _load_products(page: Page, min_items: int) -> List[Locator]:
    seen = 0
    attempts_without_growth = 0
    max_attempts = 40
    attempt = 0
    try:
        LOGGER.info("Waiting for initial product cards with selector %s", CARD_SELECTORS[0])
        await page.wait_for_selector(CARD_SELECTORS[0], timeout=30_000)
    except Exception:
        LOGGER.warning("Timed out waiting for initial product cards")
        try:
            title = await page.title()
        except Exception:
            title = "<unknown>"
        LOGGER.info("Page title at timeout: %s", title)
    while True:
        attempt += 1
        cards = await _get_product_cards(page)
        LOGGER.info("Attempt %d: located %d product candidates", attempt, len(cards))
        if len(cards) >= min_items:
            return cards
        if len(cards) == seen:
            attempts_without_growth += 1
        else:
            attempts_without_growth = 0
        LOGGER.debug(
            "Attempts without growth: %d (previously seen: %d)",
            attempts_without_growth,
            seen,
        )
        seen = len(cards)
        if attempts_without_growth > max_attempts:
            LOGGER.warning("No additional cards loaded after %d attempts", attempts_without_growth)
            LOGGER.info("Final card count before aborting: %d", len(cards))
            return cards
        LOGGER.debug("Loaded %d cards; scrolling for more", len(cards))
        await page.mouse.wheel(0, 5000)
        await page.wait_for_timeout(1500)
        try:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        except Exception:
            LOGGER.debug("Failed to scroll via window.scrollTo")
        await _click_load_more(page)
        try:
            await page.wait_for_load_state("networkidle")
        except Exception:
            LOGGER.debug("Timed out waiting for network idle after scrolling")
    return cards


async def _click_load_more(page: Page) -> None:
    targeted = page.locator("text=/더보기|더 보기|상품 더보기/")
    if await targeted.count() > 0:
        try:
            await targeted.first.click()
            await page.wait_for_timeout(1500)
            LOGGER.debug("Clicked explicit load more control")
            return
        except Exception as exc:
            LOGGER.debug("Failed to click explicit load more control: %s", exc)

    buttons = page.locator("button, a")
    count = await buttons.count()
    LOGGER.debug("Scanning %d generic buttons/links for load more text", count)
    for idx in range(count):
        button = buttons.nth(idx)
        try:
            text = (await button.inner_text()).strip()
        except Exception:
            continue
        LOGGER.debug("Button %d text snippet: %s", idx, text[:40])
        if any(keyword in text for keyword in ("더보기", "더 보기", "상품 더보기", "전체보기")):
            try:
                await button.click()
                await page.wait_for_timeout(1500)
                LOGGER.debug("Clicked load more button")
                return
            except Exception as exc:
                LOGGER.debug("Failed to click load more: %s", exc)
                continue


async def collect_products(url: str, min_items: int = 1000, headless: bool = True) -> List[Product]:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=headless)
        page = await browser.new_page()
        page.set_default_timeout(30_000)
        try:
            LOGGER.info("Navigating to %s", url)
            await page.goto(url, wait_until="networkidle")
            await page.wait_for_timeout(2000)
            LOGGER.info("Page navigation complete; beginning product discovery (min_items=%d)", min_items)
            cards = await _load_products(page, min_items)
            products: List[Product] = []
            seen_codes = set()
            for card in cards:
                product = await _extract_card_data(card)
                if product.product_code and product.product_code in seen_codes:
                    LOGGER.debug("Skipping duplicate product_code %s", product.product_code)
                    continue
                if product.product_code:
                    seen_codes.add(product.product_code)
                products.append(product)
            if len(products) > min_items:
                products = products[:min_items]
            return products
        finally:
            await browser.close()


__all__ = ["collect_products", "Product"]
