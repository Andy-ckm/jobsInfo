#!/usr/bin/env python3
"""Create a persistent, local-only Liepin browser session."""
from __future__ import annotations

import os
import re
from pathlib import Path

from playwright.sync_api import sync_playwright

CHALLENGE = re.compile(r"验证码|安全验证|访问异常|环境异常|操作频繁|登录后查看|captcha", re.I)


def main() -> int:
    profile = Path(
        os.environ.get(
            "LIEPIN_PROFILE_DIR",
            str(Path.home() / "Library" / "Application Support" / "JobSourceNode" / "liepin-profile"),
        )
    ).expanduser()
    profile.mkdir(parents=True, exist_ok=True)

    print("即将打开专用猎聘浏览器。请在窗口中正常登录并完成平台验证。")
    print(f"登录会话仅保存在本机：{profile}")
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=False,
            viewport={"width": 1440, "height": 1000},
            locale="zh-CN",
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://www.liepin.com/", wait_until="domcontentloaded", timeout=60000)
        input("完成猎聘登录后，回到终端按Enter继续：")
        try:
            body = page.locator("body").inner_text(timeout=10000)
            if CHALLENGE.search(body):
                print("当前页面仍显示登录/安全验证提示。会话已保存，但首次采集可能标记为blocked。")
            else:
                print("猎聘本地会话已保存。")
        finally:
            context.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
