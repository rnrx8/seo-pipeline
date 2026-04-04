#!/usr/bin/env python3
"""
テスト用スクリプト：リード文 + H2① だけを生成してターミナルに表示する。

使い方:
  python test_lead.py                  # 直近のジョブを自動取得
  python test_lead.py <job_id>         # job_idを指定
"""
import sys
import os
import requests
import anthropic
from dotenv import load_dotenv

load_dotenv()

# step_article.py のプロンプトをそのまま流用
from pipeline.step_article import SYSTEM_PROMPT, USER_TEMPLATE
from pipeline.ai import get_step_config
from pipeline.db import get_artifact

MODEL, _ = get_step_config("article")
MAX_TOKENS = 2000


def get_latest_job_id() -> str:
    key = os.environ["SUPABASE_KEY"]
    url = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/jobs"
    resp = requests.get(
        url,
        params={"order": "created_at.desc", "limit": "1"},
        headers={"apikey": key, "Authorization": f"Bearer {key}"},
    )
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        raise RuntimeError("ジョブが見つかりません。")
    return rows[0]["id"], rows[0].get("main_keyword", "")


def get_keyword_for_job(job_id: str) -> str:
    key = os.environ["SUPABASE_KEY"]
    url = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/jobs"
    resp = requests.get(
        url,
        params={"id": f"eq.{job_id}", "limit": "1"},
        headers={"apikey": key, "Authorization": f"Bearer {key}"},
    )
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        raise RuntimeError(f"job_id={job_id} が見つかりません。")
    return rows[0].get("main_keyword", "")


def main():
    if len(sys.argv) >= 2:
        job_id = sys.argv[1]
        keyword = get_keyword_for_job(job_id)
    else:
        job_id, keyword = get_latest_job_id()

    print(f"job_id: {job_id}  keyword: {keyword!r}")
    print("Supabaseからアーティファクトを取得中...")

    intent = get_artifact(job_id, "search_intent")
    outline = get_artifact(job_id, "outline")
    fact = get_artifact(job_id, "fact_sheet")

    user_content = USER_TEMPLATE.format(
        keyword=keyword,
        intent_text=intent["content_text"],
        outline_text=outline["content_text"],
        fact_text=fact["content_text"],
    ) + "\n\n【出力範囲の指定】リード文と最初のH2（H2直下の文章＋最初のH3まで）だけを出力してください。それ以降は書かないでください。"

    print("Claude に生成を依頼中...\n")

    client = anthropic.Anthropic()
    with client.messages.stream(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    ) as stream:
        result = stream.get_final_message()

    text = result.content[0].text

    print("---")
    print("テスト出力：リード文 + H2①")
    print(f"job_id: {job_id}")
    print("---")
    print(text)
    print("---")
    print(f"(tokens: input={result.usage.input_tokens}, output={result.usage.output_tokens})")


if __name__ == "__main__":
    main()
