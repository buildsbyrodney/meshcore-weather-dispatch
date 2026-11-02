"""Browser-level regression coverage for the county selector."""
from __future__ import annotations

import threading
from ctypes.util import find_library

import pytest
from playwright.sync_api import sync_playwright
from werkzeug.security import generate_password_hash
from werkzeug.serving import make_server

from dispatch import create_app


def browser_config(tmp_path):
    return {
        "TESTING": True,
        "secret_key": "browser-test-secret",
        "database": str(tmp_path / "browser.sqlite"),
        "admin_username": "admin",
        "admin_password_hash": generate_password_hash("browser-password"),
        "nws_user_agent": "tests@example.invalid",
        "states": ["AL", "TN"],
        "alert_types": ["Tornado Warning"],
        "counties": [],
        "max_message_bytes": 180,
        "dry_run": True,
        "transmit_enabled": False,
        "poll_seconds": 60,
        "start_poller": False,
    }


def test_county_filters_change_computed_visibility_and_keep_hidden_selection(tmp_path):
    """The real browser must honor the hidden attribute despite grid row styling."""
    if not find_library("nspr4"):
        pytest.skip("Chromium runtime libraries are unavailable in this local environment")
    app = create_app(browser_config(tmp_path))
    db = app.extensions["db"]
    db.replace_catalog(
        [
            {"ugc": "ALC089", "state": "AL", "name": "Madison", "same": "001089"},
            {"ugc": "TNC103", "state": "TN", "name": "Lincoln", "same": "047103"},
        ],
        {"source": "browser test", "retrieved_at": "2026-01-01T00:00:00+00:00", "version": "test"},
    )
    db.save_coverage(["TNC103"], {"AL", "TN"})
    server = make_server("127.0.0.1", 0, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                context = browser.new_context(viewport={"width": 1280, "height": 720})
                page = context.new_page()
                page.goto(f"http://127.0.0.1:{server.server_port}/login")
                page.fill('input[name="username"]', "admin")
                page.fill('input[name="password"]', "browser-password")
                page.click('button:has-text("Sign in")')
                page.goto(f"http://127.0.0.1:{server.server_port}/settings/coverage-counties")

                alabama = page.locator('.county-option[data-state="AL"]')
                tennessee = page.locator('.county-option[data-state="TN"]')
                assert alabama.evaluate("row => getComputedStyle(row).display") == "grid"
                assert tennessee.evaluate("row => getComputedStyle(row).display") == "grid"
                alabama.locator('input[name="county"]').check()

                page.select_option("#county-state", "AL")
                assert alabama.evaluate("row => getComputedStyle(row).display") == "grid"
                assert tennessee.evaluate("row => getComputedStyle(row).display") == "none"
                assert tennessee.locator('input[name="county"]').is_checked()

                page.fill("#county-search", "Lincoln")
                assert alabama.evaluate("row => getComputedStyle(row).display") == "none"
                assert tennessee.evaluate("row => getComputedStyle(row).display") == "none"

                page.click("#clear-county-filters")
                assert alabama.evaluate("row => getComputedStyle(row).display") == "grid"
                assert tennessee.evaluate("row => getComputedStyle(row).display") == "grid"
                assert alabama.locator('input[name="county"]').is_checked()
                assert tennessee.locator('input[name="county"]').is_checked()

                # A selected county hidden by a filter remains a successful form value.
                page.select_option("#county-state", "AL")
                page.get_by_role("button", name="Save Changes").click()
                page.wait_for_load_state("networkidle")
                assert {row["ugc"] for row in db.coverage()} == {"ALC089", "TNC103"}
                page.close()
                context.close()

                mobile_context = browser.new_context(viewport={"width": 375, "height": 667})
                mobile = mobile_context.new_page()
                mobile.goto(f"http://127.0.0.1:{server.server_port}/login")
                mobile.fill('input[name="username"]', "admin")
                mobile.fill('input[name="password"]', "browser-password")
                mobile.click('button:has-text("Sign in")')
                mobile.goto(f"http://127.0.0.1:{server.server_port}/settings/coverage-counties")
                mobile.select_option("#county-state", "TN")
                assert mobile.locator('.county-option[data-state="AL"]').evaluate("row => getComputedStyle(row).display") == "none"
                assert mobile.locator('.county-option[data-state="TN"]').evaluate("row => getComputedStyle(row).display") == "grid"
                mobile.close()
                mobile_context.close()
            finally:
                browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_transmission_statuses_and_checkbox_stay_compact_on_mobile(tmp_path,monkeypatch):
    """The settings interlocks remain legible and the checkbox stays beside its label."""
    if not find_library("nspr4"):
        pytest.skip("Chromium runtime libraries are unavailable in this local environment")
    monkeypatch.setenv("MESHCORE_SEND_ENABLED","0")
    config=browser_config(tmp_path);config["dry_run"]=False
    app=create_app(config);app.extensions["db"].save_setting("transmit_enabled","true")
    server=make_server("127.0.0.1",0,app);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch()
            try:
                context=browser.new_context(viewport={"width":375,"height":667});page=context.new_page()
                page.goto(f"http://127.0.0.1:{server.server_port}/login")
                page.fill('input[name="username"]',"admin");page.fill('input[name="password"]',"browser-password");page.click('button:has-text("Sign in")')
                page.goto(f"http://127.0.0.1:{server.server_port}/settings")
                assert page.locator(".transmission-status .interlock-status").count()==4
                cards=page.locator(".transmission-status .interlock-status").all()
                boxes=[card.bounding_box() for card in cards]
                assert all(box and box["x"] >= 0 and box["x"]+box["width"] <= 375 for box in boxes)
                assert len({round(box["y"]) for box in boxes})==2
                checkbox=page.locator('.inline-check input[type="checkbox"]').bounding_box()
                wording=page.locator(".inline-check span").bounding_box()
                assert checkbox and wording and checkbox["x"] < wording["x"]
                assert page.locator(".interlock-explanation").inner_text().startswith("Transmission is blocked:")
                context.close()
            finally:
                browser.close()
    finally:
        server.shutdown();thread.join(timeout=5)
