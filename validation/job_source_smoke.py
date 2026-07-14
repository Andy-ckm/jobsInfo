#!/usr/bin/env python3
"""Read-only smoke test for job data sources.

No credentials, cookies, CAPTCHA bypass, proxy rotation, application, greeting,
or account mutation is used. Every source is classified from actual responses.
"""

from __future__ import annotations

import csv
import json
import math
import multiprocessing as mp
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import requests

OUT = Path(os.environ.get("SMOKE_OUT", "artifacts/job-source-smoke"))
OUT.mkdir(parents=True, exist_ok=True)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36 JobSourceSmoke/1.0"
)
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
}


def clean(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    return str(value)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def http_probe(name: str, url: str, parser: Callable[[requests.Response], dict[str, Any]] | None = None) -> dict[str, Any]:
    started = time.monotonic()
    result: dict[str, Any] = {
        "source": name,
        "kind": "http_probe",
        "url": url,
        "started_at": now_iso(),
    }
    try:
        response = requests.get(url, headers=HEADERS, timeout=(10, 25), allow_redirects=True)
        body = response.text
        result.update(
            {
                "status": "success" if response.ok else "blocked_or_failed",
                "http_status": response.status_code,
                "final_url": response.url,
                "content_type": response.headers.get("content-type"),
                "content_length": len(response.content),
                "elapsed_ms": round((time.monotonic() - started) * 1000),
                "title": (re.search(r"<title[^>]*>(.*?)</title>", body, re.I | re.S).group(1).strip()[:200]
                          if re.search(r"<title[^>]*>(.*?)</title>", body, re.I | re.S) else None),
                "challenge_markers": sorted({m for m in [
                    "captcha" if re.search(r"captcha|验证码|人机验证", body, re.I) else None,
                    "login" if re.search(r"登录|sign[ -]?in|login", body, re.I) else None,
                    "risk_control" if re.search(r"访问异常|安全验证|环境异常|风控", body, re.I) else None,
                    "job_content" if re.search(r"职位|招聘|job", body, re.I) else None,
                ] if m}),
                "body_prefix": re.sub(r"\s+", " ", body[:500]).strip(),
            }
        )
        if parser and response.ok:
            result["parsed"] = clean(parser(response))
    except Exception as exc:
        result.update(
            {
                "status": "failed",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "elapsed_ms": round((time.monotonic() - started) * 1000),
            }
        )
    return result


def parse_greenhouse(response: requests.Response) -> dict[str, Any]:
    data = response.json()
    jobs = data.get("jobs", [])
    return {
        "job_count": len(jobs),
        "samples": [
            {"id": j.get("id"), "title": j.get("title"), "location": (j.get("location") or {}).get("name"), "url": j.get("absolute_url")}
            for j in jobs[:5]
        ],
    }


def parse_list_json(response: requests.Response) -> dict[str, Any]:
    data = response.json()
    if isinstance(data, list):
        rows = data
    else:
        rows = data.get("content") or data.get("postings") or data.get("results") or []
    samples = []
    for row in rows[:5]:
        samples.append(
            {
                "id": row.get("id") or row.get("uuid"),
                "title": row.get("text") or row.get("name") or row.get("title"),
                "location": row.get("categories", {}).get("location") if isinstance(row.get("categories"), dict) else row.get("location"),
                "url": row.get("hostedUrl") or row.get("applyUrl") or row.get("ref") or row.get("url"),
            }
        )
    return {"job_count": len(rows), "samples": samples}


def parse_smartrecruiters(response: requests.Response) -> dict[str, Any]:
    data = response.json()
    rows = data.get("content", [])
    return {
        "job_count": data.get("totalFound", len(rows)),
        "returned_count": len(rows),
        "samples": [
            {
                "id": r.get("id"),
                "title": r.get("name"),
                "location": ", ".join(filter(None, [
                    (r.get("location") or {}).get("city"),
                    (r.get("location") or {}).get("country"),
                ])),
                "url": r.get("ref"),
            }
            for r in rows[:5]
        ],
    }


def _jobspy_worker(queue: mp.Queue, config: dict[str, Any]) -> None:
    try:
        from jobspy import scrape_jobs

        frame = scrape_jobs(**config)
        records = [clean(r) for r in frame.head(10).to_dict(orient="records")]
        queue.put({
            "ok": True,
            "row_count": int(len(frame)),
            "columns": list(frame.columns),
            "samples": records,
        })
    except Exception as exc:
        queue.put({"ok": False, "error_type": type(exc).__name__, "error": str(exc)})


def jobspy_probe(name: str, config: dict[str, Any], timeout_seconds: int = 120) -> dict[str, Any]:
    started = time.monotonic()
    queue: mp.Queue = mp.Queue()
    process = mp.Process(target=_jobspy_worker, args=(queue, config))
    process.start()
    process.join(timeout_seconds)
    if process.is_alive():
        process.terminate()
        process.join(10)
        payload = {"ok": False, "error_type": "Timeout", "error": f"> {timeout_seconds}s"}
    elif queue.empty():
        payload = {"ok": False, "error_type": "NoResult", "error": f"worker exit={process.exitcode}"}
    else:
        payload = queue.get()

    row_count = int(payload.get("row_count", 0) or 0)
    if payload.get("ok") and row_count > 0:
        status = "success"
    elif payload.get("ok"):
        status = "zero_result"
    else:
        status = "failed"
    result = {
        "source": name,
        "kind": "jobspy",
        "status": status,
        "config": config,
        "elapsed_ms": round((time.monotonic() - started) * 1000),
        **clean(payload),
    }
    if payload.get("samples"):
        csv_path = OUT / f"{name}.csv"
        rows = payload["samples"]
        keys = sorted({key for row in rows for key in row.keys()})
        with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=keys, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        result["sample_csv"] = str(csv_path)
    return result


def main() -> int:
    results: list[dict[str, Any]] = []

    # Public ATS APIs: live structured job data without user credentials.
    results.extend([
        http_probe(
            "ats_greenhouse_openai",
            "https://boards-api.greenhouse.io/v1/boards/openai/jobs?content=true",
            parse_greenhouse,
        ),
        http_probe(
            "ats_lever_palantir",
            "https://api.lever.co/v0/postings/palantir?mode=json",
            parse_list_json,
        ),
        http_probe(
            "ats_smartrecruiters",
            "https://api.smartrecruiters.com/v1/companies/SmartRecruiters/postings?limit=10",
            parse_smartrecruiters,
        ),
    ])

    # Current China-targeted aggregator searches. Each is isolated with a hard timeout.
    jobspy_cases = [
        (
            "jobspy_indeed_suzhou",
            {
                "site_name": ["indeed"],
                "search_term": '"solutions architect" OR "cloud architect" OR Azure',
                "location": "Suzhou",
                "country_indeed": "China",
                "results_wanted": 10,
                "description_format": "markdown",
                "verbose": 1,
            },
        ),
        (
            "jobspy_indeed_shanghai",
            {
                "site_name": ["indeed"],
                "search_term": '"solutions architect" OR "cloud architect" OR "AI engineer"',
                "location": "Shanghai",
                "country_indeed": "China",
                "results_wanted": 10,
                "description_format": "markdown",
                "verbose": 1,
            },
        ),
        (
            "jobspy_google_suzhou",
            {
                "site_name": ["google"],
                "google_search_term": "solutions architect OR cloud architect OR AI deployment jobs in Suzhou China",
                "location": "Suzhou, China",
                "results_wanted": 10,
                "description_format": "markdown",
                "verbose": 1,
            },
        ),
        (
            "jobspy_linkedin_shanghai",
            {
                "site_name": ["linkedin"],
                "search_term": "Solutions Architect",
                "location": "Shanghai, China",
                "results_wanted": 5,
                "description_format": "markdown",
                "verbose": 1,
            },
        ),
    ]
    for name, config in jobspy_cases:
        results.append(jobspy_probe(name, config))

    # Domestic public search pages: status/body validation only, no login or bypass.
    domestic = [
        ("boss_public_suzhou", "https://www.zhipin.com/web/geek/job?query=%E8%A7%A3%E5%86%B3%E6%96%B9%E6%A1%88%E6%9E%B6%E6%9E%84%E5%B8%88&city=101190400"),
        ("liepin_public", "https://www.liepin.com/zhaopin/?key=%E8%A7%A3%E5%86%B3%E6%96%B9%E6%A1%88%E6%9E%B6%E6%9E%84%E5%B8%88"),
        ("zhaopin_public_suzhou", "https://sou.zhaopin.com/?kw=%E8%A7%A3%E5%86%B3%E6%96%B9%E6%A1%88%E6%9E%B6%E6%9E%84%E5%B8%88&jl=%E8%8B%8F%E5%B7%9E"),
        ("51job_public_suzhou", "https://we.51job.com/pc/search?keyword=%E8%A7%A3%E5%86%B3%E6%96%B9%E6%A1%88%E6%9E%B6%E6%9E%84%E5%B8%88&jobArea=070300"),
        ("lagou_public_suzhou", "https://www.lagou.com/wn/jobs?kd=%E8%A7%A3%E5%86%B3%E6%96%B9%E6%A1%88%E6%9E%B6%E6%9E%84%E5%B8%88&city=%E8%8B%8F%E5%B7%9E"),
    ]
    for name, url in domestic:
        results.append(http_probe(name, url))

    report = {
        "schema_version": "1",
        "generated_at": now_iso(),
        "policy": "read_only_no_credentials_no_bypass",
        "result_count": len(results),
        "summary": {
            status: sum(1 for item in results if item.get("status") == status)
            for status in ["success", "zero_result", "blocked_or_failed", "failed"]
        },
        "results": results,
    }
    report_path = OUT / "results.json"
    report_path.write_text(json.dumps(clean(report), ensure_ascii=False, indent=2), encoding="utf-8")
    print("JOB_SOURCE_SMOKE_RESULT_BEGIN")
    print(json.dumps(clean(report), ensure_ascii=False, indent=2))
    print("JOB_SOURCE_SMOKE_RESULT_END")
    return 0


if __name__ == "__main__":
    mp.set_start_method("spawn", force=True)
    raise SystemExit(main())
