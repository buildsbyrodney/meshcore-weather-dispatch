"""Browser-level regression coverage for the county selector."""
from __future__ import annotations

import threading
import json
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
        "transmit_enabled": False,
        "poll_seconds": 60,
        "start_poller": False,
        "update_state_dir": str(tmp_path / "updates"),
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


def test_manual_transmission_setting_stays_compact_on_mobile(tmp_path):
    """The manual setting remains legible and the checkbox stays beside its label."""
    if not find_library("nspr4"):
        pytest.skip("Chromium runtime libraries are unavailable in this local environment")
    config=browser_config(tmp_path)
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
                checkbox=page.locator('.inline-check input[type="checkbox"]').bounding_box()
                wording=page.locator(".inline-check span").bounding_box()
                assert checkbox and wording and checkbox["x"] < wording["x"]
                assert "Manual transmissions allowed" in page.locator("main").inner_text()
                context.close()
            finally:
                browser.close()
    finally:
        server.shutdown();thread.join(timeout=5)


def test_manage_users_page_is_usable_on_mobile(tmp_path):
    if not find_library("nspr4"):
        pytest.skip("Chromium runtime libraries are unavailable in this local environment")
    app=create_app(browser_config(tmp_path));db=app.extensions["db"]
    db.add_user("operator",generate_password_hash("operator-password"))
    server=make_server("127.0.0.1",0,app);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch()
            try:
                page=browser.new_page(viewport={"width":375,"height":667})
                page.goto(f"http://127.0.0.1:{server.server_port}/login")
                page.fill('input[name="username"]',"admin");page.fill('input[name="password"]',"browser-password");page.click('button:has-text("Sign in")')
                page.goto(f"http://127.0.0.1:{server.server_port}/settings/users")
                assert page.get_by_role("heading",name="Manage Users").is_visible()
                assert page.get_by_text("operator",exact=True).is_visible()
                assert page.locator('input[name="username"]').last.bounding_box()["width"] > 100
            finally: browser.close()
    finally: server.shutdown();thread.join(timeout=5)


def test_test_message_confirmation_checkbox_stays_inline_on_desktop_and_mobile(tmp_path):
    """The test-message confirmation uses the shared inline checkbox layout."""
    if not find_library("nspr4"):
        pytest.skip("Chromium runtime libraries are unavailable in this local environment")
    app=create_app(browser_config(tmp_path))
    server=make_server("127.0.0.1",0,app);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch()
            try:
                for width in (1280,375):
                    context=browser.new_context(viewport={"width":width,"height":667});page=context.new_page()
                    page.goto(f"http://127.0.0.1:{server.server_port}/login")
                    page.fill('input[name="username"]',"admin");page.fill('input[name="password"]',"browser-password");page.click('button:has-text("Sign in")')
                    page.goto(f"http://127.0.0.1:{server.server_port}/test-messaging")
                    checkbox=page.locator('.inline-check input[name="confirm"]').bounding_box()
                    wording=page.locator('.inline-check span').bounding_box()
                    assert checkbox and wording and checkbox["x"] < wording["x"]
                    assert abs((checkbox["y"]+checkbox["height"]/2)-(wording["y"]+wording["height"]/2)) < 2
                    context.close()
            finally:
                browser.close()
    finally:
        server.shutdown();thread.join(timeout=5)


def test_channel_and_other_checkbox_controls_stay_inline_on_desktop_and_mobile(tmp_path,monkeypatch):
    """All remaining plain checkbox labels use the shared inline layout."""
    if not find_library("nspr4"):
        pytest.skip("Chromium runtime libraries are unavailable in this local environment")
    app=create_app(browser_config(tmp_path));db=app.extensions["db"]
    db.replace_catalog([{"ugc":"ALC089","state":"AL","name":"Madison","same":"001089"}],{"source":"browser test"})
    db.save_coverage(["ALC089"], {"AL", "TN"})
    monkeypatch.setattr("dispatch.routes.device_state",lambda db: ({},{"device":"/dev/fake"},"Connected"))
    monkeypatch.setattr("dispatch.routes.discover_channels",lambda dev:{"capacity":4,"slots":[{"index":0,"name":"Public","state":"occupied"},{"index":1,"name":"","state":"empty"}]})
    monkeypatch.setattr("dispatch.routes.read_device_name",lambda dev:"WXALERT")
    server=make_server("127.0.0.1",0,app);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch()
            try:
                for width in (1280,375):
                    context=browser.new_context(viewport={"width":width,"height":667});page=context.new_page()
                    page.goto(f"http://127.0.0.1:{server.server_port}/login")
                    page.fill('input[name="username"]',"admin");page.fill('input[name="password"]',"browser-password");page.click('button:has-text("Sign in")')
                    page.goto(f"http://127.0.0.1:{server.server_port}/settings")
                    channel_check=page.locator('.add-channel .inline-check input[name="confirm"]').bounding_box()
                    channel_text=page.locator('.add-channel .inline-check span').bounding_box()
                    assert channel_check and channel_text and channel_check["x"] < channel_text["x"]
                    assert abs((channel_check["y"]+channel_check["height"]/2)-(channel_text["y"]+channel_text["height"]/2)) < 2
                    page.goto(f"http://127.0.0.1:{server.server_port}/settings/coverage-counties")
                    coverage_check=page.locator('.confirm-expand.inline-check input').bounding_box()
                    coverage_text=page.locator('.confirm-expand.inline-check span').bounding_box()
                    assert coverage_check and coverage_text and coverage_check["x"] < coverage_text["x"]
                    page.goto(f"http://127.0.0.1:{server.server_port}/test-alerts")
                    county_check=page.locator('.county-choice.inline-check input').bounding_box()
                    county_text=page.locator('.county-choice.inline-check span').bounding_box()
                    assert county_check and county_text and county_check["x"] < county_text["x"]
                    context.close()
            finally:
                browser.close()
    finally:
        server.shutdown();thread.join(timeout=5)


def test_alert_type_selector_filters_counts_and_stays_usable_on_mobile(tmp_path):
    """Alert choices are searchable, grouped, and have usable controls at Pi widths."""
    if not find_library("nspr4"):
        pytest.skip("Chromium runtime libraries are unavailable in this local environment")
    app=create_app(browser_config(tmp_path));db=app.extensions["db"]
    db.replace_alert_type_catalog(
        ["Tornado Warning","Tornado Watch","Heat Advisory","Special Weather Statement"],
        {"source":"browser test","retrieved_at":"now","version":"test"},
    )
    server=make_server("127.0.0.1",0,app);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch()
            try:
                for width in (1280,375):
                    context=browser.new_context(viewport={"width":width,"height":667});page=context.new_page()
                    page.goto(f"http://127.0.0.1:{server.server_port}/login")
                    page.fill('input[name="username"]',"admin");page.fill('input[name="password"]',"browser-password");page.click('button:has-text("Sign in")')
                    page.goto(f"http://127.0.0.1:{server.server_port}/settings/alert-types")
                    assert page.get_by_text("Warnings",exact=True).is_visible()
                    assert page.get_by_text("Watches",exact=True).is_visible()
                    page.fill("#alert-type-search","Watch")
                    assert page.locator('.alert-type-choice:has-text("Tornado Watch")').evaluate("row => getComputedStyle(row).display") != "none"
                    assert page.locator('.alert-type-choice:has-text("Tornado Warning")').evaluate("row => getComputedStyle(row).display") == "none"
                    page.click("#clear-all-alert-types")
                    assert page.locator("#enabled-count").inner_text()=="0"
                    page.click("#select-all-alert-types")
                    assert page.locator("#enabled-count").inner_text()=="4"
                    context.close()
            finally:
                browser.close()
    finally:
        server.shutdown();thread.join(timeout=5)

def test_software_updates_page_is_compact_and_requires_confirmation(tmp_path, monkeypatch):
    """The browser sees a fixed update UI, never a command-entry surface."""
    if not find_library("nspr4"):
        pytest.skip("Chromium runtime libraries are unavailable in this local environment")
    app = create_app(browser_config(tmp_path))
    monkeypatch.setattr("dispatch.routes.available_update", lambda: {"available": True, "commit": "a" * 40, "error": None})
    server = make_server("127.0.0.1", 0, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                context = browser.new_context(viewport={"width": 375, "height": 667})
                page = context.new_page()
                page.goto(f"http://127.0.0.1:{server.server_port}/login")
                page.fill('input[name="username"]', "admin")
                page.fill('input[name="password"]', "browser-password")
                page.click('button:has-text("Sign in")')
                page.goto(f"http://127.0.0.1:{server.server_port}/settings/software-updates")
                assert page.get_by_text("A reboot is sometimes necessary after an update - follow the instructions in the console.").is_visible()
                assert page.locator("#update-console").is_visible()
                assert page.locator('input[name="confirm"]').count() == 2
                assert page.locator('input[name="command"]').count() == 0
                assert page.get_by_role("button", name="Install Update").is_disabled() is False
                context.close()
            finally:
                browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)

def test_software_update_console_hides_only_a_prior_successful_run(tmp_path, monkeypatch):
    """A fresh visit clears completed output, while live and failed output remains useful."""
    if not find_library("nspr4"):
        pytest.skip("Chromium runtime libraries are unavailable in this local environment")
    config = browser_config(tmp_path)
    state = tmp_path / "updates"
    state.mkdir()
    def write_update(status, message, log):
        (state / "status.json").write_text(json.dumps({"status": status, "message": message}))
        (state / "update.log").write_text(log)
    write_update("success", "Update completed", "previous successful output")
    app = create_app(config)
    monkeypatch.setattr("dispatch.routes.available_update", lambda: {"available": True, "commit": "a" * 40, "error": None})
    server = make_server("127.0.0.1", 0, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                context = browser.new_context()
                page = context.new_page()
                page.goto(f"http://127.0.0.1:{server.server_port}/login")
                page.fill('input[name="username"]', "admin")
                page.fill('input[name="password"]', "browser-password")
                page.click('button:has-text("Sign in")')
                page.goto(f"http://127.0.0.1:{server.server_port}/settings/software-updates")
                assert page.locator("#update-console").inner_text() == ""
                write_update("running", "Update running", "live update output")
                page.wait_for_function("document.querySelector('#update-console').textContent === 'live update output'")
                write_update("success", "Update completed", "new successful output")
                page.wait_for_function("document.querySelector('#update-console').textContent === 'new successful output'")
                page.goto(f"http://127.0.0.1:{server.server_port}/settings")
                page.goto(f"http://127.0.0.1:{server.server_port}/settings/software-updates")
                assert page.locator("#update-console").inner_text() == ""
                write_update("failed", "Update failed; see console", "failure details")
                page.reload()
                assert page.locator("#update-console").inner_text() == "failure details"
                context.close()
            finally:
                browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)

def test_automatic_sending_control_confirms_before_enabling_and_warning_types_stay_aligned(tmp_path):
    if not find_library("nspr4"):
        pytest.skip("Chromium runtime libraries are unavailable in this local environment")
    app = create_app(browser_config(tmp_path))
    db = app.extensions["db"]
    db.save_setting("auto_warning_types", '["Tornado Warning"]')
    server = make_server("127.0.0.1", 0, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                context = browser.new_context(viewport={"width": 375, "height": 667})
                page = context.new_page()
                page.goto(f"http://127.0.0.1:{server.server_port}/login")
                page.fill('input[name="username"]', "admin")
                page.fill('input[name="password"]', "browser-password")
                page.click('button:has-text("Sign in")')
                page.goto(f"http://127.0.0.1:{server.server_port}/")
                assert page.locator(".automatic-status").inner_text().startswith("Automatic Sending")
                assert page.locator(".automatic-status .tag").inner_text() == "OFF"
                assert page.locator('#automatic-sending-form input[type="checkbox"]').count() == 0
                page.get_by_role("button", name="Turn ON").click()
                assert page.locator("#automatic-dialog").evaluate("dialog => dialog.open")
                page.get_by_role("button", name="Cancel").click()
                assert db.setting("auto_sending_enabled") == "false"
                page.get_by_role("button", name="Turn ON").click()
                page.get_by_role("button", name="Enable Automatic Sending").click()
                page.wait_for_function("document.querySelector('.automatic-status .tag').textContent === 'ON'")
                assert db.setting("auto_sending_enabled") == "true"
                page.get_by_role("button", name="Turn OFF").click()
                page.wait_for_function("document.querySelector('.automatic-status .tag').textContent === 'OFF'")
                assert db.setting("auto_sending_enabled") == "false"
                page.goto(f"http://127.0.0.1:{server.server_port}/settings")
                choice = page.locator(".automatic-check").first
                checkbox = choice.locator('input[type="checkbox"]')
                label = choice.locator("span")
                checkbox_box, label_box = checkbox.bounding_box(), label.bounding_box()
                assert checkbox.is_checked()
                assert checkbox_box["x"] < label_box["x"] and abs(checkbox_box["y"] - label_box["y"]) < 8
                checkbox.uncheck()
                page.get_by_role("button", name="Save automatic warning types").click()
                assert db.setting("auto_warning_types") == "[]"
                page.reload()
                assert page.locator(".automatic-check input").is_checked() is False
                context.close()
            finally:
                browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
