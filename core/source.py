from __future__ import annotations

import re
import base64
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .models import DecisionRow


DOCKET_RE = re.compile(r"(?<!\d)(\d{4}-\d{4})(?!\d)")


class USCGSource:
    def __init__(self, config: dict):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": config["user_agent"],
            "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })
        self.driver = None

    def year_url(self, year: int) -> str:
        if year < 1990 or year > 2100:
            raise ValueError("Year must be between 1990 and 2100")
        year_urls = self.config.get("source_year_urls") or {}
        explicit_url = year_urls.get(str(year)) or year_urls.get(year)
        if explicit_url:
            return str(explicit_url)
        return self.config["source_url_template"].format(year=year)

    def list_decisions(self, year: int) -> list[DecisionRow]:
        url = self.year_url(year)
        response = self.session.get(url, timeout=self.config["request_timeout_seconds"])
        if response.status_code == 403 and self.config.get("browser_fallback_enabled", True):
            html = self._browser_html(url)
        else:
            response.raise_for_status()
            html = response.text
        soup = BeautifulSoup(html, "html.parser")
        rows: list[DecisionRow] = []
        seen: set[tuple[str, str]] = set()
        for tr in soup.select("tr"):
            cells = tr.find_all(["td", "th"])
            if not cells:
                continue
            anchor = tr.find("a", href=True)
            row_text = " ".join(cell.get_text(" ", strip=True) for cell in cells)
            match = DOCKET_RE.search(row_text)
            if not match or not anchor:
                continue
            href = str(anchor.get("href") or "")
            if ".pdf" not in href.lower():
                continue
            docket = match.group(1)
            pdf_url = urljoin(url, href)
            key = (docket, pdf_url)
            if key in seen:
                continue
            seen.add(key)
            values = [cell.get_text(" ", strip=True) for cell in cells]
            rows.append(DecisionRow(
                docket=docket,
                pdf_url=pdf_url,
                visible_date=_parse_visible_date(values[1] if len(values) > 1 else ""),
                allegations=values[2] if len(values) > 2 else "",
                findings=values[3] if len(values) > 3 else "",
                order_type=values[4] if len(values) > 4 else "",
            ))
        return rows

    def download(self, url: str) -> bytes:
        response = self.session.get(url, timeout=self.config["request_timeout_seconds"])
        if response.status_code == 403 and self.config.get("browser_fallback_enabled", True):
            data, content_type = self._browser_download(url)
        else:
            response.raise_for_status()
            data = response.content
            content_type = response.headers.get("Content-Type", "").lower()
        if not data.startswith(b"%PDF") and "application/pdf" not in content_type:
            raise ValueError(f"Source did not return a PDF ({content_type or 'unknown content type'})")
        return data

    def close(self) -> None:
        if self.driver is not None:
            try:
                self.driver.quit()
            finally:
                self.driver = None

    def _ensure_browser(self):
        if self.driver is not None:
            return self.driver
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
        except ImportError as exc:
            raise RuntimeError("USCG returned 403 and Selenium browser fallback is not installed") from exc
        options = Options()
        if self.config.get("headless", True):
            options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument(f"--user-agent={self.config['user_agent']}")
        self.driver = webdriver.Chrome(options=options)
        self.driver.set_page_load_timeout(self.config["request_timeout_seconds"])
        self.driver.set_script_timeout(self.config["request_timeout_seconds"])
        return self.driver

    def _browser_html(self, url: str) -> str:
        driver = self._ensure_browser()
        driver.get(url)
        for cookie in driver.get_cookies():
            self.session.cookies.set(cookie["name"], cookie["value"], domain=cookie.get("domain"))
        return driver.page_source

    def _browser_download(self, url: str) -> tuple[bytes, str]:
        driver = self._ensure_browser()
        if "uscg.mil" not in (driver.current_url or ""):
            driver.get(self.year_url(int(re.search(r"/20\d{2}/", url).group(0).strip("/"))))
        result = driver.execute_async_script(
            """
            const url = arguments[0], done = arguments[arguments.length - 1];
            fetch(url, {credentials: 'include'}).then(async response => {
              const bytes = new Uint8Array(await response.arrayBuffer());
              let binary = '';
              const chunk = 0x8000;
              for (let i = 0; i < bytes.length; i += chunk) {
                binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
              }
              done({ok: response.ok, status: response.status, type: response.headers.get('content-type') || '', data: btoa(binary)});
            }).catch(error => done({ok: false, status: 0, error: String(error)}));
            """,
            url,
        )
        if not result or not result.get("ok"):
            raise RuntimeError(f"Browser PDF download failed (HTTP {result.get('status') if result else 'unknown'})")
        return base64.b64decode(result["data"]), str(result.get("type") or "")


def _parse_visible_date(value: str):
    try:
        return datetime.strptime(value.strip(), "%m/%d/%Y").date()
    except ValueError:
        return None
