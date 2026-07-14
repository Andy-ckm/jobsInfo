#!/usr/bin/env python3
"""Source health control plane and authorized local BOSS/Liepin collectors.

All collectors are read-only. A login challenge, CAPTCHA, safety-verification
page, or risk-control response stops the source and is recorded as `blocked`;
it is never reported as a legitimate zero-result run.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "validation" / "source_registry.json"
STATES = {"success", "zero_result", "blocked", "condition_unmet", "failed", "not_run"}
CHALLENGE = re.compile(
    r"验证码|安全验证|访问异常|环境异常|IP地址存在异常|操作频繁|登录后查看|captcha",
    re.I,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def local_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def safe_slug(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", value).strip("_") or "query"


def validate_registry(registry: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    executors = registry.get("executors") or {}
    checks = registry.get("health_checks") or {}
    sources = registry.get("sources") or []
    seen: set[str] = set()
    expected = {
        "SRC001", "SRC002", "SRC003", "SRC004", "SRC005", "SRC006",
        "SRC007", "SRC008", "SRC009", "SRC010", "SRC011", "SRC012",
        "SRC016", "SRC017", "SRC018", "SRC019", "SRC020", "SRC021",
        "SRC026", "SRC027", "SRC028", "SRC029", "SRC030", "SRC031", "SRC032",
    }

    if registry.get("schema_version") != "2.0":
        errors.append("schema_version must be 2.0")
    for index, source in enumerate(sources):
        prefix = f"sources[{index}]"
        source_id = source.get("source_id")
        if not source_id:
            errors.append(f"{prefix}: source_id required")
        elif source_id in seen:
            errors.append(f"{prefix}: duplicate source_id {source_id}")
        else:
            seen.add(source_id)
        if source.get("priority") not in {"P0", "P1"}:
            errors.append(f"{prefix}: priority must be P0/P1")
        if source.get("executor_id") not in executors:
            errors.append(f"{prefix}: unknown executor")
        health = source.get("health_check_ids")
        if not isinstance(health, list) or not health:
            errors.append(f"{prefix}: health_check_ids required")
        else:
            for check_id in health:
                if check_id not in checks:
                    errors.append(f"{prefix}: unknown health check {check_id}")
        if not source.get("runtime"):
            errors.append(f"{prefix}: runtime required")
        policy = source.get("policy") or {}
        required_policy = {
            "read_only": True,
            "stop_on_challenge": True,
            "allow_auto_apply": False,
            "allow_messaging": False,
        }
        for key, value in required_policy.items():
            if policy.get(key) is not value:
                errors.append(f"{prefix}: policy.{key} must be {value}")

    if seen != expected:
        errors.append(
            f"P0/P1 coverage mismatch missing={sorted(expected-seen)} extra={sorted(seen-expected)}"
        )
    if len(sources) != 25:
        errors.append(f"expected 25 sources, got {len(sources)}")
    return errors


def http_health(url: str) -> dict[str, Any]:
    try:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "JobSourceHealth/2.0", "Accept": "application/json,text/html,*/*"},
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            body = response.read(4096).decode("utf-8", "replace")
            return {
                "state": "blocked" if CHALLENGE.search(body) else "success",
                "http_status": response.status,
                "url": response.url,
            }
    except urllib.error.HTTPError as exc:
        return {"state": "failed", "http_status": exc.code, "error": str(exc), "url": url}
    except Exception as exc:
        return {"state": "failed", "error": f"{type(exc).__name__}: {exc}", "url": url}


def drop_health() -> dict[str, Any]:
    raw = os.environ.get("JOB_SOURCE_DROP_DIR", "").strip()
    if not raw:
        return {"state": "condition_unmet", "reason": "JOB_SOURCE_DROP_DIR not configured"}
    path = Path(raw).expanduser()
    if not path.exists():
        return {"state": "condition_unmet", "reason": "drop directory missing", "path": str(path)}
    writable = os.access(path, os.W_OK)
    return {"state": "success" if writable else "failed", "path": str(path), "writable": writable}


def boss_health() -> dict[str, Any]:
    executable = shutil.which("boss") or shutil.which("boss.exe")
    if not executable:
        return {"state": "condition_unmet", "reason": "boss-cli not installed"}
    try:
        process = subprocess.run(
            [executable, "status", "--json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
        )
        start = process.stdout.find("{")
        data = json.loads(process.stdout[start:]) if start >= 0 else {}
        authenticated = bool(data.get("authenticated"))
        return {
            "state": "success" if authenticated else "condition_unmet",
            "authenticated": authenticated,
            "search_authenticated": data.get("search_authenticated"),
        }
    except Exception as exc:
        return {"state": "failed", "error": f"{type(exc).__name__}: {exc}"}


def liepin_health() -> dict[str, Any]:
    profile = Path(
        os.environ.get(
            "LIEPIN_PROFILE_DIR",
            str(Path.home() / ".job-source-node" / "liepin-profile"),
        )
    ).expanduser()
    try:
        import playwright  # noqa: F401
    except Exception:
        return {"state": "condition_unmet", "reason": "playwright not installed", "profile": str(profile)}
    return {
        "state": "success" if profile.exists() else "condition_unmet",
        "profile": str(profile),
        "profile_exists": profile.exists(),
    }


def source_health(source: dict[str, Any], registry: dict[str, Any], context: str) -> dict[str, Any]:
    executor_id = source["executor_id"]
    kind = registry["executors"][executor_id]["kind"]
    if context not in source["runtime"]:
        detail = {"state": "condition_unmet", "reason": f"not configured for {context}"}
    elif executor_id == "boss_local_authorized":
        detail = boss_health()
    elif executor_id == "liepin_local_authorized":
        detail = liepin_health()
    elif executor_id in {"domestic_local_browser", "get_jobs_readonly_import", "drive_snapshot_ingest"}:
        detail = drop_health()
    elif executor_id == "jobspy_mcp":
        endpoint = os.environ.get("JOBSPY_MCP_ENDPOINT", "").strip()
        detail = http_health(endpoint) if endpoint else {
            "state": "condition_unmet", "reason": "JOBSPY_MCP_ENDPOINT not configured"
        }
    elif executor_id in {
        "official_career_fanout", "greenhouse_api", "lever_api",
        "smartrecruiters_api", "company_site_connector",
    }:
        config_path = ROOT / "config" / "target_companies.json"
        if not config_path.exists():
            detail = {"state": "condition_unmet", "reason": "target company config missing"}
        else:
            config = load_json(config_path)
            key_map = {
                "official_career_fanout": "official_careers",
                "greenhouse_api": "greenhouse",
                "lever_api": "lever",
                "smartrecruiters_api": "smartrecruiters",
                "company_site_connector": "company_sites",
            }
            config_key = key_map[executor_id]
            count = len(config.get(config_key) or [])
            detail = {
                "state": "success" if count else "condition_unmet",
                "target_count": count,
                "reason": None if count else f"no {config_key} targets",
            }
    elif executor_id == "jobspy_executor":
        try:
            import jobspy  # noqa: F401
            detail = {"state": "success", "dependency": "jobspy"}
        except Exception:
            detail = {"state": "condition_unmet", "reason": "python-jobspy not installed"}
    elif executor_id == "web_search_discovery":
        detail = {"state": "success", "reason": "provided by scheduled cloud runtime"}
    else:
        detail = {"state": "not_run", "reason": f"no health implementation for {kind}"}

    return {
        "source_id": source["source_id"],
        "source_name": source["source_name"],
        "priority": source["priority"],
        "executor_id": executor_id,
        "executor_kind": kind,
        "state": detail.get("state", "failed"),
        "detail": detail,
        "checked_at": utc_now(),
    }


def make_job_key(source_id: str, title: str, company: str, location: str, url: str) -> str:
    value = "|".join([source_id, title.lower().strip(), company.lower().strip(), location.lower().strip(), url.strip()])
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def write_source_output(directory: Path, rows: list[dict[str, Any]], health: dict[str, Any]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "jobs.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    (directory / "health.json").write_text(json.dumps(health, ensure_ascii=False, indent=2), encoding="utf-8")
    fields = [
        "source_id", "source_name", "job_key", "title", "company", "salary_text",
        "location", "source_url", "description", "published_at", "collected_at",
    ]
    with (directory / "jobs.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def collect_boss(config: dict[str, Any], output: Path) -> dict[str, Any]:
    executable = shutil.which("boss") or shutil.which("boss.exe")
    if not executable:
        health = {"state": "condition_unmet", "reason": "boss-cli not installed", "checked_at": local_now()}
        write_source_output(output, [], health)
        return health
    auth = boss_health()
    if auth["state"] != "success":
        health = {"state": "condition_unmet", "reason": "run boss login locally", "auth": auth, "checked_at": local_now()}
        write_source_output(output, [], health)
        return health

    rows: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    blocked = False
    raw_dir = output / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    for city in config.get("cities", ["苏州", "上海"]):
        for keyword in config.get("keywords", ["解决方案架构师", "云架构师", "Azure", "AI解决方案", "FDE"]):
            path = raw_dir / (city + "_" + safe_slug(keyword) + ".json")
            process = subprocess.run(
                [
                    executable, "export", keyword, "--city", city,
                    "-n", str(config.get("count", 30)), "--format", "json", "-o", str(path),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=600,
            )
            challenge = bool(CHALLENGE.search(process.stdout + "\n" + process.stderr))
            attempts.append({
                "city": city, "keyword": keyword, "returncode": process.returncode,
                "challenge": challenge,
            })
            if challenge:
                blocked = True
                break
            try:
                payload = load_json(path) if path.exists() else []
            except Exception:
                payload = []
            if isinstance(payload, list):
                for item in payload:
                    title = str(item.get("jobName") or "")
                    company = str(item.get("brandName") or "")
                    location = " ".join(filter(None, [
                        str(item.get("cityName") or ""), str(item.get("areaDistrict") or ""),
                    ]))
                    source_url = str(item.get("jobUrl") or item.get("securityId") or "")
                    if not title:
                        continue
                    rows.append({
                        "source_id": "SRC016",
                        "source_name": "BOSS直聘",
                        "job_key": make_job_key("SRC016", title, company, location, source_url),
                        "title": title,
                        "company": company,
                        "salary_text": str(item.get("salaryDesc") or ""),
                        "location": location,
                        "source_url": source_url,
                        "description": "",
                        "published_at": "",
                        "collected_at": local_now(),
                    })
            time.sleep(float(config.get("delay_seconds", 3)))
        if blocked:
            break

    rows = list({row["job_key"]: row for row in rows}.values())
    (output / "attempts.json").write_text(json.dumps(attempts, ensure_ascii=False, indent=2), encoding="utf-8")
    health = {
        "state": "blocked" if blocked else ("success" if rows else "zero_result"),
        "job_count": len(rows),
        "attempts": len(attempts),
        "stopped_on_challenge": blocked,
        "checked_at": local_now(),
    }
    write_source_output(output, rows, health)
    return health


def find_job_dicts(value: Any, output: list[dict[str, Any]]) -> None:
    if isinstance(value, dict):
        keys = {str(key).lower() for key in value}
        has_title = any(key in keys for key in ("jobtitle", "jobname", "title", "positionname"))
        has_company = any(key in keys for key in ("companyname", "company", "brandname", "compname"))
        if has_title and (has_company or any("salary" in key for key in keys)):
            output.append(value)
        for child in value.values():
            find_job_dicts(child, output)
    elif isinstance(value, list):
        for child in value:
            find_job_dicts(child, output)


def pick(item: dict[str, Any], *names: str) -> str:
    lowered = {str(key).lower(): value for key, value in item.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value is not None and not isinstance(value, (dict, list)):
            return str(value)
    return ""


def collect_liepin(config: dict[str, Any], output: Path) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        health = {"state": "condition_unmet", "reason": "playwright not installed", "checked_at": local_now()}
        write_source_output(output, [], health)
        return health

    profile = Path(
        os.environ.get(
            "LIEPIN_PROFILE_DIR",
            str(Path.home() / ".job-source-node" / "liepin-profile"),
        )
    ).expanduser()
    profile.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    blocked = False

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=bool(config.get("headless", False)),
            viewport={"width": 1440, "height": 1000},
            locale="zh-CN",
        )
        page = context.pages[0] if context.pages else context.new_page()
        captured: list[Any] = []

        def capture(response: Any) -> None:
            content_type = (response.headers.get("content-type") or "").lower()
            if "json" in content_type and re.search(r"job|search|position|recruit", response.url, re.I):
                try:
                    captured.append(response.json())
                except Exception:
                    pass

        page.on("response", capture)
        for city in config.get("cities", ["苏州", "上海"]):
            for keyword in config.get("keywords", ["解决方案架构师", "云架构师", "Azure", "AI解决方案", "FDE"]):
                captured.clear()
                search_url = "https://www.liepin.com/zhaopin/?key=" + quote(keyword + " " + city)
                try:
                    page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(5000)
                    body = page.locator("body").inner_text(timeout=10000)
                    challenge = bool(CHALLENGE.search(body))
                    attempts.append({
                        "city": city, "keyword": keyword, "url": page.url,
                        "challenge": challenge, "json_responses": len(captured),
                    })
                    screenshots = output / "screenshots"
                    screenshots.mkdir(parents=True, exist_ok=True)
                    screenshot_name = city + "_" + safe_slug(keyword) + ".png"
                    page.screenshot(path=str(screenshots / screenshot_name))
                    if challenge:
                        blocked = True
                        break

                    candidates: list[dict[str, Any]] = []
                    for payload in captured:
                        find_job_dicts(payload, candidates)
                    nodes = page.locator(
                        "a[href*='/job/'],a[href*='job-detail'],.job-card,.job-list-item,[class*='job-card']"
                    )
                    for index in range(min(nodes.count(), int(config.get("dom_limit", 60)))):
                        try:
                            node = nodes.nth(index)
                            text = re.sub(r"\s+", " ", node.inner_text(timeout=2000)).strip()
                            href = node.get_attribute("href") or ""
                        except Exception:
                            continue
                        if len(text) > 3:
                            candidates.append({
                                "title": text.split(" ")[0], "company": "", "salary": "",
                                "location": city, "url": href, "description": text[:500],
                            })

                    for item in candidates:
                        title = pick(item, "jobTitle", "jobName", "title", "positionName")
                        company = pick(item, "companyName", "company", "brandName", "compName")
                        salary = pick(item, "salary", "salaryDesc", "salaryText")
                        location = pick(item, "location", "city", "cityName", "dq") or city
                        source_url = pick(item, "url", "jobUrl", "link", "href")
                        if source_url.startswith("/"):
                            source_url = "https://www.liepin.com" + source_url
                        if not title:
                            continue
                        rows.append({
                            "source_id": "SRC017",
                            "source_name": "猎聘",
                            "job_key": make_job_key("SRC017", title, company, location, source_url),
                            "title": title,
                            "company": company,
                            "salary_text": salary,
                            "location": location,
                            "source_url": source_url,
                            "description": pick(item, "description", "jobDesc", "content"),
                            "published_at": pick(item, "publishTime", "publishedAt", "refreshTime"),
                            "collected_at": local_now(),
                        })
                except Exception as exc:
                    attempts.append({
                        "city": city, "keyword": keyword, "url": search_url,
                        "error": f"{type(exc).__name__}: {exc}",
                    })
                time.sleep(float(config.get("delay_seconds", 4)))
            if blocked:
                break
        context.close()

    rows = list({row["job_key"]: row for row in rows}.values())
    (output / "attempts.json").write_text(json.dumps(attempts, ensure_ascii=False, indent=2), encoding="utf-8")
    health = {
        "state": "blocked" if blocked else ("success" if rows else "zero_result"),
        "job_count": len(rows),
        "attempts": len(attempts),
        "stopped_on_challenge": blocked,
        "profile": str(profile),
        "checked_at": local_now(),
    }
    write_source_output(output, rows, health)
    return health


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["validate", "health", "collect-local"])
    parser.add_argument("--context", choices=["cloud", "local"], default="cloud")
    parser.add_argument("--registry", default=str(REGISTRY))
    parser.add_argument("--config", default=str(ROOT / "local_node" / "config.example.json"))
    parser.add_argument("--sources", default="boss,liepin")
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    registry = load_json(Path(args.registry))
    errors = validate_registry(registry)
    if args.command == "validate":
        report: dict[str, Any] = {
            "valid": not errors,
            "errors": errors,
            "source_count": len(registry.get("sources", [])),
            "executor_count": len(registry.get("executors", {})),
            "health_check_count": len(registry.get("health_checks", {})),
            "generated_at": utc_now(),
        }
    elif args.command == "health":
        if errors:
            report = {"valid": False, "errors": errors, "generated_at": utc_now()}
        else:
            results = [source_health(source, registry, args.context) for source in registry["sources"]]
            report = {
                "valid": True,
                "context": args.context,
                "source_count": len(results),
                "summary": {state: sum(item["state"] == state for item in results) for state in STATES},
                "results": results,
                "generated_at": utc_now(),
            }
    else:
        if errors:
            report = {"valid": False, "errors": errors, "generated_at": utc_now()}
        else:
            config = load_json(Path(args.config))
            drop_value = (
                args.output
                or os.environ.get("JOB_SOURCE_DROP_DIR", "")
                or config.get("drop_dir", "")
                or str(ROOT / "job-source-drop")
            )
            drop = Path(drop_value).expanduser()
            run_id = "LOCAL-" + datetime.now().strftime("%Y%m%d-%H%M%S")
            run_dir = drop / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            selected = {value.strip() for value in args.sources.split(",") if value.strip()}
            results = []
            if "boss" in selected:
                results.append({
                    "source_id": "SRC016", "source_name": "BOSS直聘",
                    **collect_boss(config.get("boss", {}), run_dir / "boss"),
                })
            if "liepin" in selected:
                results.append({
                    "source_id": "SRC017", "source_name": "猎聘",
                    **collect_liepin(config.get("liepin", {}), run_dir / "liepin"),
                })
            report = {
                "valid": True,
                "run_id": run_id,
                "mode": "authorized_local_read_only",
                "results": results,
                "summary": {state: sum(item.get("state") == state for item in results) for state in STATES},
                "generated_at": local_now(),
            }
            (run_dir / "manifest.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            report["archive"] = shutil.make_archive(str(run_dir), "zip", root_dir=run_dir)

    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.output and args.command != "collect-local":
        Path(args.output).write_text(text, encoding="utf-8")
    return 0 if report.get("valid") else 1


if __name__ == "__main__":
    raise SystemExit(main())
