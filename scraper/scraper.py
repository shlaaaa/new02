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


async def _get_product_cards(page: Page) -> List[Locator]:
    card_selectors = [
        "li.prod-item",
        "li[class*='prd']",
        "div.product-item",
        "li",
    ]
    for selector in card_selectors:
        cards = page.locator(selector)
        count = await cards.count()
        if count >= 1:
            LOGGER.debug("Located %d cards with selector %s", count, selector)
            return [cards.nth(i) for i in range(count)]
    return []


async def _load_products(page: Page, min_items: int) -> List[Locator]:
    seen = 0
    attempts_without_growth = 0
    max_attempts = 20
    while True:
        cards = await _get_product_cards(page)
        if len(cards) >= min_items:
            return cards
        if len(cards) == seen:
            attempts_without_growth += 1
        else:
            attempts_without_growth = 0
        seen = len(cards)
        if attempts_without_growth > max_attempts:
            LOGGER.warning("No additional cards loaded after %d attempts", attempts_without_growth)
            return cards
        LOGGER.debug("Loaded %d cards; scrolling for more", len(cards))
        await page.mouse.wheel(0, 5000)
        await page.wait_for_timeout(1500)
        await _click_load_more(page)
    return cards


async def _click_load_more(page: Page) -> None:
    buttons = page.locator("button, a")
    count = await buttons.count()
    for idx in range(count):
        button = buttons.nth(idx)
        try:
            text = (await button.inner_text()).strip()
        except Exception:
            continue
        if "더보기" in text or "보기" in text:
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
            cards = await _load_products(page, min_items)
            products: List[Product] = []
            seen_codes = set()
            for card in cards:
                product = await _extract_card_data(card)
                if product.product_code and product.product_code in seen_codes:
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
