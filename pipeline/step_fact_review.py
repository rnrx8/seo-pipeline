from .fresh_sources import FreshSources, load_settings, load_primary_sources, run_with_fetch
from .db import get_job
from .content_quality import ContentQualityError, digest
import re

import anthropic

from .ai import create_with_retry, get_step_config
from .db import get_artifact, upsert_artifact
from .source_freshness import SOURCE_FRESHNESS_POLICY, SOURCE_POLICY_VERSION, current_check_date, freshness_context

MODEL, MAX_TOKENS = get_step_config("fact_review")

VERIFY_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 20}
AUDIT_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 8}

VERIFY_SYSTEM_PROMPT = SOURCE_FRESHNESS_POLICY + "\n" + """\
あなたはSEO記事の主任ファクトチェッカーです。完成記事を主張単位で監査し、証跡を残してください。

【必須フロー】
1. 外部検証可能な主張をすべて抽出する。数値・統計・料金・日付・制度・固有名詞・企業/サービスの仕様を優先する
2. 各主張を個別にweb_searchで確認する。似た主張を一括で確認済みにしない
3. 主張ごとに、判定・根拠URL・発行主体・公開/更新日・根拠箇所・確認日を記録する
4. 誤りは正しい内容へ修正し、未確認情報は削除する。記事の理解に不可欠な場合だけ「確認できる公表資料は見つかりませんでした」と明示する
5. 修正済み記事と検証レポートを出力する

【判定】
- VERIFIED_T1: 公式、政府、行政、法令原文、査読論文、統計原典で直接確認
- VERIFIED_T2: 独立した一般ソース2件以上で同じ条件・数値を確認
- CORRECTED: 記事の誤りを原典に基づいて修正
- REMOVED: 十分な根拠がなく削除
- UNVERIFIED: 検証できず、断定を記事に残していない

【独立性と条件】
- 転載、同じ一次情報の紹介、同一配信記事は複数ソースに数えない
- 数値は発表主体・対象期間・対象地域・母集団・単位が一致した場合だけ確認済みにする
- 検索結果の要約だけでなく、ページ本文の該当箇所を確認する
- 料金、プラン、機能、制度、法律、税、補助金、求人件数、医療、金融、安全情報は公式・行政・原典必須
- 更新性の高い情報には「YYYY年MM月時点」を付ける
- 出典があっても主張を直接支えていなければ確認済みにしない

【禁止】
- 未確認情報を「と言われています」などに弱めるだけで残すこと
- URLや根拠箇所を捏造すること
- 記事の主題・構成・文体を不必要に変更すること

【出力形式】
===ARTICLE_START===
修正済みMarkdown記事
===ARTICLE_END===
===FACTCHECK_REPORT_START===
## 検証サマリー
- 検証対象: N件
- 確認済み: N件
- 修正: N件
- 削除: N件
- 未確認: N件

## 主張別の証跡
### Claim 1
- 元の主張: ...
- 判定: VERIFIED_T1 / VERIFIED_T2 / CORRECTED / REMOVED / UNVERIFIED
- 修正後: ...
- 根拠: URL
- 発行主体: ...
- 公開・更新日: ...
- 確認箇所: 「今回取得した本文から根拠となる連続した短い原文（8〜240文字）」
- 独立性・条件確認: ...
===FACTCHECK_REPORT_END===
"""

AUDIT_SYSTEM_PROMPT = SOURCE_FRESHNESS_POLICY + "\n" + """\
あなたは最終品質監査者です。すでにファクトチェックされた記事と検証レポートを再検査してください。
記事に残る外部検証可能な主張がレポートで裏付けられているかを照合し、必要な場合だけweb_searchで再確認してください。
裏付けのない断定は削除し、誤修正は原典に基づいて直してください。新しい事実や数値を追加してはいけません。

===ARTICLE_START===
最終版Markdown記事
===ARTICLE_END===
===FINAL_AUDIT_START===
- 監査結果: PASS または CHANGED
- 再確認した主張: N件
- 追加修正: N件
- 詳細: ...
本文の事実を変更した場合は、変更した主張ごとに次の形式を必ず追加する。
### Claim（元のレポートのClaim番号）
- 判定: CORRECTED
- 修正後: 最終記事に採用した事実（変更後の値・適用条件を明記）
- 根拠: URL
- 確認箇所: 「直接取得した本文の連続した原文」
===FINAL_AUDIT_END===
"""


def _parse_block(text: str, start: str, end: str) -> str | None:
    match = re.search(rf"{re.escape(start)}\s*\n(.*?)\n{re.escape(end)}", text, re.DOTALL)
    return match.group(1).strip() if match else None


def _run_search_pass(client, *, system: str, prompt: str, tool: dict, fresh):
    report_tag = "FINAL_AUDIT" if "===FINAL_AUDIT_START===" in system else "FACTCHECK_REPORT"
    prompt += ("\n\n出力は必ず===ARTICLE_START===と===ARTICLE_END===で囲んだ記事全文、および"
               f"==={report_tag}_START===と==={report_tag}_END===で囲んだレポート全文を含めてください。"
               "変更がなくても本文を省略しないでください。確認箇所のラベルは各主張につき『確認箇所:』に統一してください。")
    resp, raw, queries, _ = run_with_fetch(
        client, create=create_with_retry, model=MODEL, max_tokens=MAX_TOKENS,
        system=system, prompt=prompt, search_tool=tool, fresh=fresh,
    )
    required = ("===ARTICLE_START===", "===ARTICLE_END===", f"==={report_tag}_START===", f"==={report_tag}_END===")
    if any(marker not in raw for marker in required):
        repaired, raw, more_queries, _ = run_with_fetch(
            client, create=create_with_retry, model=MODEL, max_tokens=MAX_TOKENS,
            system=system, prompt=prompt + "\n前回の応答は必須の出力ブロックが不足しています。全ブロックを省略せず再出力してください。\n" + raw,
            search_tool=tool, fresh=fresh,
        )
        repaired.usage.input_tokens += resp.usage.input_tokens
        repaired.usage.output_tokens += resp.usage.output_tokens
        resp = repaired
        queries.extend(more_queries)
    return resp, raw, queries


def _require_direct_evidence(report, fresh):
    report = report.replace("**", "").replace("`", "")
    for block in re.split(r"\n(?=###?\s)", report):
        if re.search(r"判定[：:]\s*(?:VERIFIED_T[12]|CORRECTED)", block) and not fresh.evidence_matches(block):
            raise ValueError("確認済み判定に直接取得した本文の根拠がありません。元の記事を保持しました。")


def _verified_facts(report: str, fresh) -> dict[str, str]:
    """Forward only adopted claims, never the superseded original claim."""
    result = {}
    for block in re.split(r'\n(?=###?\s)', '\n' + report.replace('**', '').replace('`', '')):
        status = re.search(r'判定[：:]\s*(VERIFIED_T[12]|CORRECTED)', block)
        if not status:
            continue
        def field(label):
            match = re.search(rf'^\s*-\s*{label}[：:]\s*(.+?)(?=\n\s*-\s*[^\n：:]+[：:]|\Z)', block, re.M | re.S)
            return match[1].strip() if match else ''
        claim = field('修正後')
        if status[1].startswith('VERIFIED') and (not claim or claim in ('変更なし', '原文のまま', '同上')):
            claim = field('元の主張')
        urls = field('根拠(?:URL)?')
        quote = field('確認箇所(?:（[^）]*）)?')
        heading = re.search(r'^###?\s*(.+)$', block, re.M)
        if not claim or not urls or not quote or not fresh.evidence_matches(block):
            raise ContentQualityError('確認済みの主張を次工程へ渡すための事実・出典・引用が不足しています。')
        key_match = re.search(r'Claim\s*[（(]?\s*(\d+)', heading[1] if heading else '', re.I)
        key = key_match[1] if key_match else claim
        result[key] = f'> {claim}\n> 出典：{urls}\n> 確認箇所：{quote}\n> [confirmed]'
    return result


def run(job_id: str, keyword: str, api_key: str | None = None) -> dict:
    """Verify the completed article claim-by-claim, persist evidence, then audit it again."""
    print("[fact_review] Starting claim-level verification...")
    original = get_artifact(job_id, "article")["content_text"]
    client = anthropic.Anthropic(api_key=api_key)
    checked_on = current_check_date()
    job = get_job(job_id)
    fresh = FreshSources(job, load_settings(job, load_primary_sources(job)))
    fresh.prefetch(extra=original)
    fresh.save(job_id, "fresh_sources_review")

    try:
        verify_resp, verify_raw, verify_queries = _run_search_pass(
            client,
            system=VERIFY_SYSTEM_PROMPT,
            prompt=freshness_context(checked_on) + f"キーワード: {keyword}\n\n## 完成記事\n{original}",
            tool=VERIFY_SEARCH_TOOL, fresh=fresh,
        )
    finally:
        fresh.save(job_id, "fresh_sources_review")
    verified_article = _parse_block(verify_raw, "===ARTICLE_START===", "===ARTICLE_END===")
    report = _parse_block(verify_raw, "===FACTCHECK_REPORT_START===", "===FACTCHECK_REPORT_END===")
    verify_truncated = getattr(verify_resp, "stop_reason", None) == "max_tokens"
    if verify_truncated or not verified_article or not report:
        raise ValueError("Fact review response was incomplete; original article was preserved")

    fresh.save(job_id, "fresh_sources_review")
    _require_direct_evidence(report, fresh)

    audit_prompt = (
        freshness_context(checked_on) +
        f"## 検証後の記事\n{verified_article}\n\n"
        f"## 主張別検証レポート\n{report}"
    )
    try:
        audit_resp, audit_raw, audit_queries = _run_search_pass(
            client,
            system=AUDIT_SYSTEM_PROMPT,
            prompt=audit_prompt,
            tool=AUDIT_SEARCH_TOOL, fresh=fresh,
        )
    finally:
        fresh.save(job_id, "fresh_sources_review")
    final_article = _parse_block(audit_raw, "===ARTICLE_START===", "===ARTICLE_END===")
    audit_report = _parse_block(audit_raw, "===FINAL_AUDIT_START===", "===FINAL_AUDIT_END===")
    audit_truncated = getattr(audit_resp, "stop_reason", None) == "max_tokens"
    if audit_truncated or not final_article or not audit_report:
        raise ValueError("Final fact audit response was incomplete; original article was preserved")

    fresh.save(job_id, "fresh_sources_review")
    _require_direct_evidence(audit_report, fresh)
    adopted = _verified_facts(report, fresh)
    final_corrections = _verified_facts(audit_report, fresh)
    if re.search(r'監査結果[：:]\s*CHANGED', audit_report) and not final_corrections:
        raise ContentQualityError('最終ファクト監査の訂正根拠が不足しています。')
    adopted.update(final_corrections)
    evidence_text = '\n\n'.join(adopted.values())

    full_report = f"# 強化ファクトチェックレポート\n\n{report}\n\n## 最終再検査\n{audit_report}"
    all_queries = verify_queries + audit_queries
    upsert_artifact(
        job_id=job_id,
        step="fact_review",
        content_type="text/markdown",
        content_text=full_report,
        meta={
            "model": MODEL,
            "verification_search_queries": verify_queries,
            "audit_search_queries": audit_queries,
            "source_policy_version": SOURCE_POLICY_VERSION,
            "checked_on": checked_on,
            "input_tokens": verify_resp.usage.input_tokens + audit_resp.usage.input_tokens,
            "output_tokens": verify_resp.usage.output_tokens + audit_resp.usage.output_tokens,
        },
    )
    upsert_artifact(job_id=job_id, step='fact_review_evidence', content_type='text/markdown',
                    content_text=evidence_text,
                    meta={'base_fact_sha256': digest(get_artifact(job_id, 'fact_sheet')['content_text']),
                          'source_policy_version': SOURCE_POLICY_VERSION})
    artifact = upsert_artifact(
        job_id=job_id,
        step="article",
        content_type="text/markdown",
        content_text=final_article,
        meta={
            "model": MODEL,
            "fact_reviewed": True,
            "final_fact_audited": True,
            "fact_review_evidence_sha256": digest(evidence_text),
            "search_queries": all_queries,
        },
    )
    print(f"[fact_review] Done ({len(all_queries)} recorded searches) → artifact id={artifact['id']}")
    return artifact
