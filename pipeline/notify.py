"""Discord + Email (Resend) dual-channel alerting for pipeline events."""
import os

import requests as req

DISCORD_ALERT_WEBHOOK_URL = os.getenv("DISCORD_ALERT_WEBHOOK_URL", "")
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
ALERT_EMAIL = os.getenv("ALERT_EMAIL", "nishi.aworks@gmail.com")


def _discord(title: str, body: str, color: int) -> None:
    if not DISCORD_ALERT_WEBHOOK_URL:
        return
    try:
        req.post(
            DISCORD_ALERT_WEBHOOK_URL,
            json={"embeds": [{"title": title, "description": body, "color": color}]},
            timeout=10,
        )
    except Exception as exc:
        print(f"[notify] Discord failed: {exc}")


def _email(subject: str, html: str) -> None:
    if not RESEND_API_KEY:
        return
    try:
        req.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
            json={
                "from": "SEO Pipeline <onboarding@resend.dev>",
                "to": [ALERT_EMAIL],
                "subject": subject,
                "html": html,
            },
            timeout=10,
        )
    except Exception as exc:
        print(f"[notify] Email failed: {exc}")


def alert_stale_job(job_id: str, keyword: str, user_email: str, minutes: int) -> None:
    instruction = (
        f"seo-saasのジョブ（{keyword}）が queued のまま{minutes}分以上止まっています。"
        f"job_id: {job_id} の原因を調べて再実行してください"
    )
    discord_body = (
        f"**キーワード**: {keyword}\n"
        f"**ユーザー**: {user_email or '不明'}\n"
        f"**job\\_id**: `{job_id}`\n"
        f"**状態**: queued で {minutes} 分以上停止\n\n"
        f"**Claudeへの指示**\n```\n{instruction}\n```"
    )
    html = f"""
<h2>⚠️ ジョブが止まっています</h2>
<table>
  <tr><td><b>キーワード</b></td><td>{keyword}</td></tr>
  <tr><td><b>ユーザー</b></td><td>{user_email or '不明'}</td></tr>
  <tr><td><b>job_id</b></td><td><code>{job_id}</code></td></tr>
  <tr><td><b>状態</b></td><td>queued で {minutes} 分以上停止</td></tr>
</table>
<hr>
<p><b>Claudeへの指示（コピペ）:</b></p>
<pre style="background:#f4f4f4;padding:12px">{instruction}</pre>
"""
    _discord("⚠️ ジョブが止まっています", discord_body, 0xFFA500)
    _email(f"[SEO Pipeline] ジョブ停止: {keyword}", html)


def alert_failed_job(job_id: str, keyword: str, user_email: str, error_msg: str = "", failed_step: str | None = None) -> None:
    instruction = (
        f"seo-saasのジョブ（{keyword}）が failed になりました。"
        f"job_id: {job_id} の失敗原因を調べて修正してください"
    )
    step_line = f"**失敗ステップ**: `{failed_step}`\n" if failed_step else ""
    error_line = f"**エラー**:\n```\n{error_msg[:500]}\n```\n" if error_msg else ""
    discord_body = (
        f"**キーワード**: {keyword}\n"
        f"**ユーザー**: {user_email or '不明'}\n"
        f"**job\\_id**: `{job_id}`\n"
        f"{step_line}"
        f"{error_line}\n"
        f"**Claudeへの指示**\n```\n{instruction}\n```"
    )
    step_row = f"<tr><td><b>失敗ステップ</b></td><td><code>{failed_step}</code></td></tr>" if failed_step else ""
    error_block = f"<pre style='background:#fff0f0;padding:12px;color:#991b1b'>{error_msg[:500]}</pre>" if error_msg else ""
    html = f"""
<h2>❌ ジョブが失敗しました</h2>
<table>
  <tr><td><b>キーワード</b></td><td>{keyword}</td></tr>
  <tr><td><b>ユーザー</b></td><td>{user_email or '不明'}</td></tr>
  <tr><td><b>job_id</b></td><td><code>{job_id}</code></td></tr>
  {step_row}
</table>
{f'<hr><p><b>エラー内容:</b></p>{error_block}' if error_block else ''}
<hr>
<p><b>Claudeへの指示（コピペ）:</b></p>
<pre style="background:#f4f4f4;padding:12px">{instruction}</pre>
"""
    _discord("❌ ジョブが失敗しました", discord_body, 0xFF0000)
    _email(f"[SEO Pipeline] ジョブ失敗: {keyword}", html)
