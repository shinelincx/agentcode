"""Browser verification for opt-in prototype golden cases."""
import os


def check_preview(html: str, spec: dict) -> list[dict]:
    from playwright.sync_api import sync_playwright

    checks: list[dict] = []
    requests: list[str] = []
    page_errors: list[str] = []
    origin = "https://prototype.invalid/"

    def check(label: str, ok: bool, detail: str = "") -> None:
        checks.append({"desc": label, "ok": ok, "detail": detail})

    def route_request(route) -> None:
        if route.request.url == origin and route.request.is_navigation_request():
            route.fulfill(status=200, body=html, content_type="text/html; charset=utf-8")
        else:
            requests.append(route.request.url)
            route.abort()

    def block_socket(socket) -> None:
        requests.append(socket.url)
        socket.close()

    with sync_playwright() as pw:
        with pw.chromium.launch(channel=os.getenv("AGENTCODE_PLAYWRIGHT_CHANNEL") or None) as browser:
            with browser.new_context(viewport={"width": 1120, "height": 720},
                                     offline=True, service_workers="block") as context:
                context.route("**/*", route_request)
                context.route_web_socket("**/*", block_socket)
                page = context.new_page()
                page.set_default_timeout(2500)
                page.on("pageerror", lambda error: page_errors.append(str(error)))
                page.goto(origin, wait_until="load")
                for css, label in (spec.get("visible") or {}).items():
                    item = page.locator(css)
                    actual = item.inner_text() if item.count() == 1 and item.is_visible() else ""
                    check(f"页面可见 {css}", label in actual, actual)
                if anchor := spec.get("purple_sidebar"):
                    purple_left = page.locator(anchor).evaluate(r"""node => {
                        for (let item = node; item; item = item.parentElement) {
                            const bounds = item.getBoundingClientRect();
                            const [r, g, b] = getComputedStyle(item).backgroundColor.match(/\d+/g).map(Number);
                            if (bounds.left < innerWidth / 4 && b > r && r > g && b - g > 25) return true;
                        }
                        return false;
                    }""")
                    check("桌面品牌位于紫色左栏", purple_left)
                for action in spec.get("actions", []):
                    for attempt in range(1, action.get("repeat", 1) + 1):
                        page.locator(action["click"]).click()
                        target = page.locator(action["target"])
                        actual = target.inner_text() if target.count() == 1 and target.is_visible() else ""
                        check(f"第 {attempt} 次点击 {action['click']} 后显示 {action['text']}",
                              action["text"] in actual, actual)
                for width in (1120, 390):
                    page.set_viewport_size({"width": width, "height": 720})
                    page.reload(wait_until="load")
                    check(f"{width}px 视口无横向溢出", page.evaluate(
                        "document.documentElement.scrollWidth <= window.innerWidth"))
                check("预览无外部资源请求", not requests, "；".join(requests)[:350])
                check("预览无页面脚本错误", not page_errors, "；".join(page_errors)[:350])
    return checks
