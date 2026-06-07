#!/usr/bin/env python3
"""検索意図(step_intent)の単体検証スクリプト。

使い方:
    python3 verify_intent.py "転職エージェント おすすめ 30代"

serp -> intent だけを実行し、コール①markdown / intent_chains / query_attrs を
ダンプして簡易自動チェック（approach起点・マズロー到達・chains本数）を出す。
"""
import json
import os
import sys

from dotenv import load_dotenv

load_dotenv()

REQUIRED_ENV = ["SUPABASE_URL", "SUPABASE_KEY", "SERPAPI_KEY", "ANTHROPIC_API_KEY"]


def main() -> None:
    if len(sys.argv) < 2:
        print('使い方: python3 verify_intent.py "対策キーワード"')
        sys.exit(1)
    missing = [k for k in REQUIRED_ENV if not os.getenv(k)]
    if missing:
        print(f"環境変数が不足: {', '.join(missing)}（.env を確認）")
        sys.exit(1)

    from pipeline.db import insert_job, get_artifact
    from pipeline import step_serp, step_intent

    kw = sys.argv[1]
    job_id = insert_job(kw)
    print(f"\n>>> job_id={job_id}  KW={kw!r}\n")

    step_serp.run(job_id, kw)
    step_intent.run(job_id, kw)

    print("\n===== (1) search_intent : コール① markdown =====\n")
    print(get_artifact(job_id, "search_intent")["content_text"])

    print("\n===== (2) intent_chains : コール② JSON =====\n")
    try:
        ch = get_artifact(job_id, "intent_chains")
        data = json.loads(ch["content_text"])
        chains = data.get("chains", [])
        print(json.dumps(data, ensure_ascii=False, indent=2))
        appr = sum(1 for c in chains if c.get("direction") == "approach")
        avoid = sum(1 for c in chains if c.get("direction") == "avoidance")
        reached = sum(1 for c in chains if c.get("maslow") not in (None, "", "未到達"))
        print(f"\n[自動チェック] chains={len(chains)}本 / approach={appr} / avoidance={avoid} / マズロー到達={reached}")
        print("  -> 期待: 4〜6本 / approach>=1 / 到達>=3")
    except Exception as e:
        print("intent_chains なし（コール②失敗・警告ログのみで継続）:", e)

    print("\n===== (3) query_attrs : 起点ヒント / UIカード互換 =====\n")
    print(get_artifact(job_id, "query_attrs")["content_text"])


if __name__ == "__main__":
    main()
