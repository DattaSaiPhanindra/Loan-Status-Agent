import asyncio
import time
from abc import ABC, abstractmethod
from typing import Literal

from playwright.async_api import Locator, Page
from pydantic import BaseModel


class UIElement(BaseModel):
    """A single interactive element observed on the surface."""

    role: str
    name: str
    value: str = ""
    description: str = ""
    ref: str


class Observation(BaseModel):
    """Snapshot of the current surface state."""

    elements: list[UIElement]
    raw_text: str
    url: str
    timestamp: float


class Action(BaseModel):
    """An action to perform on the surface. Discriminated union via action_type."""

    action_type: Literal["click", "type", "select", "navigate", "wait", "scroll"]
    ref: str = ""
    value: str = ""
    description: str = ""


class ActionResult(BaseModel):
    """Result of performing an action."""

    success: bool
    message: str = ""
    error: str = ""


class Surface(ABC):
    """Abstract interface for observing and acting on any UI surface."""

    @abstractmethod
    async def observe(self) -> Observation:
        """Capture current state of the surface."""

    @abstractmethod
    async def act(self, action: Action) -> ActionResult:
        """Perform an action on the surface."""

    @abstractmethod
    async def screenshot(self) -> bytes:
        """Capture screenshot as PNG bytes for evidence."""

    @abstractmethod
    async def current_url(self) -> str:
        """Current location identifier (URL, window title, etc)."""

    @abstractmethod
    async def close(self) -> None:
        """Release resources."""


class PlaywrightSurface(Surface):
    def __init__(self, page: Page):
        self._page = page
        self._element_map: dict[str, Locator] = {}

    def _build_dom_locator(self, raw: dict, index: int) -> Locator:
        """Build a Playwright locator from raw DOM element info, preferring stable selectors."""
        tag = raw["tag"]
        type_attr = raw["type"]
        name_attr = raw["name_attr"]
        id_attr = raw["id_attr"]
        role = raw["role"]
        text = raw["text"]

        if id_attr:
            return self._page.locator(f"#{id_attr}")

        if name_attr and tag in ("input", "textarea", "select"):
            selector = f'{tag}[name="{name_attr}"]'
            if type_attr:
                selector = f'{tag}[name="{name_attr}"][type="{type_attr}"]'
            return self._page.locator(selector)

        if role == "button":
            if tag == "input" and type_attr == "submit" and raw.get("name", ""):
                return self._page.locator(f'input[type="submit"][value="{raw["name"]}"]')
            if text:
                return self._page.get_by_role("button", name=text)

        if role == "link" and text:
            return self._page.get_by_role("link", name=text).first

        selector = tag
        if type_attr:
            selector = f'{tag}[type="{type_attr}"]'
        return self._page.locator(selector).nth(index)

    async def observe(self) -> Observation:
        self._element_map = {}
        url = self._page.url
        try:
            raw_text = await self._page.inner_text("body")
        except Exception:
            raw_text = ""
        raw_text = raw_text[:2000]

        elements: list[UIElement] = []
        try:
            raw_els = await self._page.evaluate(
                """() => {
                const results = [];
                document.querySelectorAll(
                    'input, button, select, textarea, a[href], [role="button"], [role="link"], [onclick]'
                ).forEach((el, i) => {
                    const tag = el.tagName.toLowerCase();
                    const type = (el.type || '').toLowerCase();
                    const name_attr = el.getAttribute('name') || '';
                    const id_attr = el.id || '';
                    const value = el.value || '';
                    const text = (el.textContent || '').trim().substring(0, 100);
                    const href = el.href || '';
                    const aria_label = el.getAttribute('aria-label') || '';
                    const placeholder = el.getAttribute('placeholder') || '';

                    let role = 'unknown';
                    if (tag === 'input' && ['text', 'email', 'search', 'tel', 'url', 'number'].includes(type)) role = 'textbox';
                    else if (tag === 'input' && type === 'password') role = 'textbox';
                    else if (tag === 'input' && type === 'submit') role = 'button';
                    else if (tag === 'input' && type === 'checkbox') role = 'checkbox';
                    else if (tag === 'input' && type === 'radio') role = 'radio';
                    else if (tag === 'textarea') role = 'textbox';
                    else if (tag === 'button') role = 'button';
                    else if (tag === 'select') role = 'combobox';
                    else if (tag === 'a') role = 'link';
                    else if (el.getAttribute('role')) role = el.getAttribute('role');
                    else if (el.onclick || el.getAttribute('onclick')) role = 'button';

                    let display_name = aria_label || name_attr || placeholder || '';
                    if (role === 'button') display_name = value || text || display_name;
                    if (role === 'link') display_name = text || href || display_name;

                    results.push({
                        role: role,
                        name: display_name,
                        value: (role === 'textbox' || role === 'combobox') ? value : '',
                        tag: tag,
                        type: type,
                        name_attr: name_attr,
                        id_attr: id_attr,
                        href: href,
                        text: text
                    });
                });
                return results;
            }"""
            )

            for i, raw in enumerate(raw_els):
                ref = f"e{i}"
                elements.append(
                    UIElement(
                        role=raw["role"],
                        name=raw["name"],
                        value=raw.get("value", ""),
                        ref=ref,
                    )
                )
                self._element_map[ref] = self._build_dom_locator(raw, i)

        except Exception:
            elements = []
            self._element_map = {}

        return Observation(
            elements=elements,
            raw_text=raw_text,
            url=url,
            timestamp=time.time(),
        )

    async def act(self, action: Action) -> ActionResult:
        try:
            if action.action_type == "click":
                locator = self._element_map[action.ref]
                await locator.click(timeout=5000)
            elif action.action_type == "type":
                locator = self._element_map[action.ref]
                await locator.fill(action.value, timeout=5000)
            elif action.action_type == "navigate":
                await self._page.goto(action.value, timeout=10000)
            elif action.action_type == "wait":
                seconds = float(action.value) if action.value else 1.0
                await asyncio.sleep(min(seconds, 5.0))
            elif action.action_type == "select":
                locator = self._element_map[action.ref]
                await locator.select_option(action.value, timeout=5000)
            elif action.action_type == "scroll":
                await self._page.evaluate("window.scrollBy(0, 300)")
            return ActionResult(success=True, message=f"{action.action_type} completed")
        except KeyError:
            return ActionResult(
                success=False,
                error=f"Element ref '{action.ref}' not found in current observation",
            )
        except Exception as e:
            return ActionResult(success=False, error=f"{type(e).__name__}: {str(e)}")

    async def screenshot(self) -> bytes:
        return await self._page.screenshot(type="png")

    async def current_url(self) -> str:
        return self._page.url

    async def close(self) -> None:
        pass


if __name__ == "__main__":
    obs = Observation(elements=[], raw_text="test", url="http://test", timestamp=0.0)
    act = Action(action_type="click", ref="e0")
    print(f"Surface models OK: {obs}, {act}")
