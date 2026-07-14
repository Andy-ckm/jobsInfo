#!/usr/bin/env python3
"""Best-effort, read-only BOSS Zhipin public job collector.

Safety boundaries:
- no user credentials, cookies, QR codes, CAPTCHA solving, proxy rotation, stealth plugins,
  greetings, applications, chat, or account mutations;
- uses an ordinary Chromium browser and public search pages only;
- stops at security challenges and records evidence instead of bypassing them.
"""
from __future__ import annotations

import csv
import html
import json
import os
import random
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from playwright.sync_api import BrowserContext, Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

OUT = Path(os.environ.get("BOSS_OUT", "artifacts/boss-public"))
OUT.mkdir(parents=True, exist_ok=True)

CITIES = {"苏州": "101190400", "上海": "101020100"}
KEYWORDS = [
    "解决方案架构师",
    "云架构师",
    "Azure",
    "AI解决方案",
    "人工智能工程师",
    "RAG",
    "Agent",
    "身份安全",
    "零信任",
    "客户工程师",
    "Forward Deployed Engineer",
    "FDE",
    "数据架构师",
    "售前架构师",
    "技术顾问",
]
DETAIL_LIMIT = 24

CHALLENGE_PATTERNS = {
    "captcha": r"验证码|captcha|人机验证|滑块",
    "security": r"安全验证|环境异常|访问异常|风控|请求异常",
    "login": r"登录后|立即登录|扫码登录|手机号登录",
    "too_frequent": r"访问过于频繁|请求频繁|稍后再试",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", html.unescape(str(value))).strip()


def challenge_markers(text: str) -> list[str]:
    return [name for name, pattern in CHALLENGE_PATTERNS.items() if re.search(pattern, text, re.I)]


def safe_json(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        return None


def extract_job_lists(value: Any) -> list[dict[str, Any]]:
    """Recursively extract BOSS jobList/cardList arrays without assuming one payload version."""
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"jobList", "cardList"} and isinstance(child, list):
                found.extend(item for item in child if isinstance(item, dict))
            else:
                found.extend(extract_job_lists(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(extract_job_lists(child))
    return found


def normalize_job(raw: dict[str, Any], *, city: str, keyword: str, source: str) -> dict[str, Any]:
    skills = raw.get("skills") or raw.get("showSkills") or raw.get("jobLabels") or []
    if isinstance(skills, str):
        skills = [skills]
    location_parts = [raw.get("cityName"), raw.get("areaDistrict"), raw.get("businessDistrict")]
    return {
        "source": source,
        "query_city": city,
        "query_keyword": keyword,
        "job_name": clean_text(raw.get("jobName") or raw.get("title") or raw.get("positionName")),
        "company": clean_text(raw.get("brandName") or raw.get("companyName") or raw.get("brandComName")),
        "salary": clean_text(raw.get("salaryDesc") or raw.get("salary")),
        "experience": clean_text(raw.get("jobExperience") or raw.get("experienceName")),
        "degree": clean_text(raw.get("jobDegree") or raw.get("degreeName")),
        "city": clean_text(raw.get("cityName") or city),
        "location": " ".join(clean_text(v) for v in location_parts if clean_text(v)),
        "skills": [clean_text(v) for v in skills if clean_text(v)],
        "security_id": clean_text(raw.get("securityId")),
        "lid": clean_text(raw.get("lid")),
        "encrypt_job_id": clean_text(raw.get("encryptJobId") or raw.get("jobId")),
        "boss_name": clean_text(raw.get("bossName")),
        "boss_title": clean_text(raw.get("bossTitle")),
        "job_url": clean_text(raw.get("jobUrl") or raw.get("url")),
        "raw": raw,
    }


def dom_jobs(page: Page, city: str, keyword: str) -> list[dict[str, Any]]:
    selectors = [".job-card-wrapper", ".search-job-result li", ".job-list-box li", "li.job-card-wrapper"]
    cards = None
    for selector in selectors:
        loc = page.locator(selector)
        try:
            if loc.count() > 0:
                cards = loc
                break
        except Exception:
            continue
    if cards is None:
        return []

    rows: list[dict[str, Any]] = []
    for i in range(min(cards.count(), 50)):
        card = cards.nth(i)
        def txt(*choices: str) -> str:
            for choice in choices:
                try:
                    el = card.locator(choice).first
                    if el.count():
                        value = clean_text(el.inner_text(timeout=1000))
                        if value:
                            return value
                except Exception:
                    pass
            return ""

        href = ""
        try:
            anchor = card.locator("a").first
            if anchor.count():
                href = clean_text(anchor.get_attribute("href"))
                if href.startswith("/"):
                    href = "https://www.zhipin.com" + href
        except Exception:
            pass
        rows.append({
            "source": "boss_dom",
            "query_city": city,
            "query_keyword": keyword,
            "job_name": txt(".job-name", ".job-title", "a"),
            "company": txt(".company-name", ".boss-name"),
            "salary": txt(".salary", ".job-salary"),
            "experience": txt(".tag-list", ".job-info"),
            "degree": "",
            "city": city,
            "location": txt(".job-area", ".company-location"),
            "skills": [],
            "security_id": "",
            "lid": "",
            "encrypt_job_id": "",
            "boss_name": "",
            "boss_title": "",
            "job_url": href,
            "raw": {"text": clean_text(card.inner_text(timeout=2000))},
        })
    return [row for row in rows if row["job_name"]]


def browser_fetch(page: Page, path: str, params: dict[str, Any]) -> dict[str, Any]:
    query = urllib.parse.urlencode(params)
    url = f"{path}?{query}"
    return page.evaluate(
        """async ({url}) => {
            try {
                const r = await fetch(url, {
                    method: 'GET',
                    credentials: 'include',
                    headers: {
                        'Accept': 'application/json, text/plain, */*',
                        'X-Requested-With': 'XMLHttpRequest'
                    }
                });
                return {status: r.status, final_url: r.url, text: await r.text()};
            } catch (e) {
                return {status: 0, final_url: url, text: String(e)};
            }
        }""",
        {"url": url},
    )


def collect_searches(context: BrowserContext) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    page = context.new_page()
    jobs: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    network_payloads: list[dict[str, Any]] = []

    def on_response(response: Any) -> None:
        if "/wapi/zpgeek/search/joblist.json" not in response.url:
            return
        try:
            text = response.text()
        except Exception as exc:
            network_payloads.append({"url": response.url, "status": response.status, "error": str(exc)})
            return
        network_payloads.append({
            "url": response.url,
            "status": response.status,
            "content_type": response.headers.get("content-type", ""),
            "text": text[:2_000_000],
        })

    page.on("response", on_response)

    try:
        page.goto("https://www.zhipin.com/", wait_until="domcontentloaded", timeout=45_000)
        time.sleep(3)
    except Exception:
        pass

    screenshot_count = 0
    for city_name, city_code in CITIES.items():
        for keyword in KEYWORDS:
            started = time.monotonic()
            encoded = urllib.parse.urlencode({"query": keyword, "city": city_code})
            search_url = f"https://www.zhipin.com/web/geek/job?{encoded}"
            attempt: dict[str, Any] = {
                "city": city_name,
                "city_code": city_code,
                "keyword": keyword,
                "search_url": search_url,
                "started_at": now_iso(),
            }
            before_network = len(network_payloads)
            try:
                response = page.goto(search_url, wait_until="domcontentloaded", timeout=45_000)
                attempt["page_http_status"] = response.status if response else None
                time.sleep(4)
                try:
                    page.mouse.wheel(0, 900)
                    time.sleep(1)
                except Exception:
                    pass
                body = clean_text(page.locator("body").inner_text(timeout=5000))
                attempt.update({
                    "final_url": page.url,
                    "title": page.title(),
                    "body_length": len(body),
                    "body_prefix": body[:1000],
                    "challenge_markers": challenge_markers(body),
                })

                # First consume JSON responses generated by the ordinary page itself.
                current_payloads = network_payloads[before_network:]
                network_jobs = 0
                for payload in current_payloads:
                    parsed = safe_json(payload.get("text", ""))
                    rows = extract_job_lists(parsed)
                    network_jobs += len(rows)
                    jobs.extend(normalize_job(row, city=city_name, keyword=keyword, source="boss_page_network") for row in rows)
                attempt["page_network_job_count"] = network_jobs

                # Then issue the same read-only search request inside the page session.
                api_result = browser_fetch(page, "/wapi/zpgeek/search/joblist.json", {
                    "query": keyword,
                    "city": city_code,
                    "page": 1,
                    "pageSize": 30,
                })
                attempt["api_status"] = api_result.get("status")
                attempt["api_text_prefix"] = clean_text(api_result.get("text", ""))[:500]
                parsed_api = safe_json(api_result.get("text", ""))
                api_rows = extract_job_lists(parsed_api)
                attempt["api_job_count"] = len(api_rows)
                jobs.extend(normalize_job(row, city=city_name, keyword=keyword, source="boss_session_api") for row in api_rows)

                dom_rows = dom_jobs(page, city_name, keyword)
                attempt["dom_job_count"] = len(dom_rows)
                jobs.extend(dom_rows)

                if screenshot_count < 8 and (api_rows or dom_rows or attempt["challenge_markers"]):
                    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", f"{city_name}_{keyword}")[:80]
                    shot = OUT / f"page_{safe_name}.png"
                    page.screenshot(path=str(shot), full_page=False)
                    attempt["screenshot"] = str(shot)
                    screenshot_count += 1
            except PlaywrightTimeoutError as exc:
                attempt.update({"error_type": "Timeout", "error": str(exc), "final_url": page.url})
            except Exception as exc:
                attempt.update({"error_type": type(exc).__name__, "error": str(exc), "final_url": page.url})
            attempt["elapsed_ms"] = round((time.monotonic() - started) * 1000)
            attempts.append(attempt)

            # Ordinary, modest pacing. This is not an anti-detection mechanism.
            time.sleep(2.0 + random.random())

    (OUT / "network_payloads.json").write_text(
        json.dumps(network_payloads, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    page.close()
    return jobs, attempts


def enrich_details(context: BrowserContext, jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    page = context.new_page()
    enriched: list[dict[str, Any]] = []
    seen: set[str] = set()
    for job in jobs:
        sid = job.get("security_id", "")
        if not sid or sid in seen or len(enriched) >= DETAIL_LIMIT:
            continue
        seen.add(sid)
        result = browser_fetch(page, "/wapi/zpgeek/job/detail.json", {
            "securityId": sid,
            "lid": job.get("lid", ""),
        })
        parsed = safe_json(result.get("text", ""))
        detail = parsed.get("zpData", {}) if isinstance(parsed, dict) else {}
        enriched.append({
            "security_id": sid,
            "http_status": result.get("status"),
            "response_code": parsed.get("code") if isinstance(parsed, dict) else None,
            "message": parsed.get("message") if isinstance(parsed, dict) else None,
            "detail": detail,
            "text_prefix": clean_text(result.get("text", ""))[:500],
        })
        time.sleep(2)
    page.close()
    return enriched


def collect_indexed_results() -> list[dict[str, Any]]:
    """Fallback: collect search-engine-indexed links that point to zhipin.com."""
    rows: list[dict[str, Any]] = []
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 JobSourceAudit/1.0"})
    queries = [
        "site:zhipin.com 苏州 解决方案架构师",
        "site:zhipin.com 苏州 Azure AI 工程师",
        "site:zhipin.com 上海 云架构师 AI 客户工程师",
        "site:zhipin.com 上海 FDE Forward Deployed Engineer",
    ]
    for query in queries:
        url = "https://www.bing.com/search?format=rss&q=" + urllib.parse.quote_plus(query)
        try:
            response = session.get(url, timeout=25)
            root = ET.fromstring(response.text)
            for item in root.findall(".//item"):
                link = clean_text(item.findtext("link"))
                if "zhipin.com" not in link:
                    continue
                rows.append({
                    "query": query,
                    "title": clean_text(item.findtext("title")),
                    "link": link,
                    "description": clean_text(item.findtext("description")),
                    "http_status": response.status_code,
                })
        except Exception as exc:
            rows.append({"query": query, "error_type": type(exc).__name__, "error": str(exc)})
    return rows


def dedupe_jobs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        key = row.get("security_id") or "|".join([
            clean_text(row.get("job_name")).lower(),
            clean_text(row.get("company")).lower(),
            clean_text(row.get("location") or row.get("city")).lower(),
        ])
        if not key.strip("|") or key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "source", "query_city", "query_keyword", "job_name", "company", "salary",
        "experience", "degree", "city", "location", "skills", "security_id", "lid",
        "encrypt_job_id", "boss_name", "boss_title", "job_url",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            out = dict(row)
            out["skills"] = ", ".join(row.get("skills", []))
            writer.writerow(out)


def main() -> int:
    started = now_iso()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            viewport={"width": 1440, "height": 1000},
        )
        jobs, attempts = collect_searches(context)
        unique_jobs = dedupe_jobs(jobs)
        details = enrich_details(context, unique_jobs)
        cookie_names = sorted(cookie["name"] for cookie in context.cookies())
        context.close()
        browser.close()

    indexed = collect_indexed_results()
    write_csv(OUT / "boss_jobs.csv", unique_jobs)
    (OUT / "boss_jobs.json").write_text(json.dumps(unique_jobs, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "boss_attempts.json").write_text(json.dumps(attempts, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "boss_details.json").write_text(json.dumps(details, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "boss_indexed_results.json").write_text(json.dumps(indexed, ensure_ascii=False, indent=2), encoding="utf-8")

    report = {
        "schema_version": "1",
        "started_at": started,
        "finished_at": now_iso(),
        "policy": "read_only_public_no_credentials_no_bypass_no_account_actions",
        "cities": CITIES,
        "keywords": KEYWORDS,
        "attempt_count": len(attempts),
        "raw_job_count": len(jobs),
        "unique_job_count": len(unique_jobs),
        "detail_attempt_count": len(details),
        "detail_success_count": sum(1 for item in details if item.get("response_code") == 0),
        "indexed_link_count": sum(1 for item in indexed if item.get("link")),
        "challenge_attempt_count": sum(1 for item in attempts if item.get("challenge_markers")),
        "api_success_attempt_count": sum(1 for item in attempts if item.get("api_job_count", 0) > 0),
        "dom_success_attempt_count": sum(1 for item in attempts if item.get("dom_job_count", 0) > 0),
        "page_network_success_attempt_count": sum(1 for item in attempts if item.get("page_network_job_count", 0) > 0),
        "cookie_names_only": cookie_names,
        "sample_jobs": [{k: row.get(k) for k in ["job_name", "company", "salary", "location", "query_keyword", "source"]} for row in unique_jobs[:20]],
    }
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("BOSS_PUBLIC_COLLECT_RESULT_BEGIN")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("BOSS_PUBLIC_COLLECT_RESULT_END")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
