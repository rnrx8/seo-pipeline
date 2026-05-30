"""Error classification and GitHub auto-fix trigger."""
import os
import re

import requests

_STEP_TO_FILE = {
    "serp":          "pipeline/step_serp.py",
    "search_intent": "pipeline/step_intent.py",
    "fact_sheet":    "pipeline/step_fact_sheet.py",
    "outline":       "pipeline/step_outline.py",
    "article":       "pipeline/step_article.py",
    "review":        "pipeline/step_review.py",
}

_RULES = [
    (re.compile(r"credit balance|billing|insufficient_quota|exceeded.*quota", re.I),
     False, "operational", "APIクレジット不足"),
    (re.compile(r"rate.?limit|too many requests|429", re.I),
     True,  "transient",   "レートリミット"),
    (re.compile(r"timeout|timed out|connection.?reset|connection.?refused|503|502|network", re.I),
     True,  "transient",   "接続エラー／タイムアウト"),
    (re.compile(r"AttributeError|KeyError|TypeError|IndexError|JSONDecodeError|json\.decoder|UnboundLocalError|NameError", re.I),
     False, "code_bug",    "コードバグ（例外型）"),
    (re.compile(r"artifact not found|no rows", re.I),
     False, "code_bug",    "データ参照エラー"),
]


def classify_error(exc: Exception) -> dict:
    """Return {retryable, type, reason} for routing retry / autofix."""
    msg = f"{type(exc).__name__}: {exc}"
    for pattern, retryable, err_type, reason in _RULES:
        if pattern.search(msg):
            return {"retryable": retryable, "type": err_type, "reason": reason, "detail": msg}
    # デフォルト: 不明なエラーは一時的とみなしてリトライ
    return {"retryable": True, "type": "transient", "reason": "不明なエラー（デフォルトリトライ）", "detail": msg}


def create_github_issue(
    job_id: str,
    keyword: str,
    error_msg: str,
    failed_step: str | None,
    diagnosis: dict,
) -> None:
    """Create a GitHub Issue with 'bugfix' label to trigger the auto-fix workflow."""
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        print("[autofix] GITHUB_TOKEN not set — skipping issue creation")
        return

    file_hint = _STEP_TO_FILE.get(failed_step or "", "（特定不可）")
    title = f"[自動バグ報告] {failed_step or '不明'} ステップでエラー"
    body = f"""\
## 概要

パイプラインでコードバグが検出されました。自動修正をお願いします。

## エラー情報

| 項目 | 内容 |
|---|---|
| ジョブID | `{job_id}` |
| キーワード | {keyword} |
| 失敗ステップ | `{failed_step or '不明'}` |
| 関連ファイル | `{file_hint}` |
| 診断 | {diagnosis.get('reason', '')} |

## エラー詳細

```
{error_msg[:1500]}
```

## 修正の方針

1. `{file_hint}` を開いてエラー箇所を特定する
2. 最小限の修正を加える
3. 修正できない場合はこの Issue にコメントを残す

*このIssueはパイプラインによって自動作成されました。*
"""

    resp = requests.post(
        "https://api.github.com/repos/rnrx8/seo-pipeline/issues",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
        json={"title": title, "body": body, "labels": ["bugfix"]},
        timeout=10,
    )
    if resp.status_code == 201:
        print(f"[autofix] GitHub Issue created: {resp.json().get('html_url')}")
    else:
        print(f"[autofix] Issue creation failed: {resp.status_code} {resp.text[:200]}")
