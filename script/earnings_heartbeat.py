#!/usr/bin/env python3
"""Explicitly enabled hourly progress reporting; never starts research or calls a model."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

from earnings_quota_guard import refresh
from earnings_common import ROOT, atomic_write_json, load_config, read_json, utc_now
from earnings_delivery import deliver, destination, exclusive_lock, prepare_notification, runtime_path


def connect_readonly(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db


def snapshot(root: Path, config: dict, industry_ids: list[str]) -> dict:
    universe = read_json(root / config["paths"]["universe"])
    industries = [r for r in universe["industries"] if r["industry_id"] in industry_ids]
    symbols = sorted({r["symbol"] for group in industries for r in group["issuers"]})
    if len(industries) != len(set(industry_ids)):
        raise ValueError("heartbeat industry scope differs from configured universe")
    window = config["disclosure_window"]
    if not window: raise ValueError("heartbeat requires an explicit disclosure window")
    with connect_readonly(root / "runtime/earnings/continuation.sqlite") as db:
        row = db.execute("SELECT * FROM rounds ORDER BY revision DESC LIMIT 1").fetchone()
        round_row = dict(row) if row else {}
        workers = [dict(r) for r in db.execute("SELECT * FROM workers WHERE round_id=? AND state='running'", (round_row.get("round_id"),))]
    live = []
    for worker in workers:
        try:
            command = Path(f"/proc/{int(worker['pid'])}/cmdline").read_bytes().split(b'\0')
            if any(b'earnings_continuation.py' in arg for arg in command) and str(round_row['round_id']).encode() in command:
                live.append(worker["pid"])
        except (OSError, ValueError): pass
    company_reports = {}; industry_reports = {}; publications = []
    with connect_readonly(root / config["paths"]["state"]) as db:
        rows = db.execute("""SELECT a.*,i.symbol FROM report_artifacts a
            JOIN research_tasks t ON t.task_id=a.task_id
            JOIN earnings_events e ON e.event_id=a.subject_id JOIN issuers i ON i.issuer_id=e.issuer_id
            WHERE a.report_type='company' AND a.source_mode='live' AND t.state='completed'
            AND (SELECT MIN(date(COALESCE(d.published_at,d.accepted_at))) FROM documents d
                 WHERE d.event_id=e.event_id AND COALESCE(d.form,'') NOT LIKE '%/A')>=?
            AND (SELECT MIN(date(COALESCE(d.published_at,d.accepted_at))) FROM documents d
                 WHERE d.event_id=e.event_id AND COALESCE(d.form,'') NOT LIKE '%/A')<?
            ORDER BY a.rowid DESC""", (window["start"],window["end_exclusive"])).fetchall()
        for row in rows:
            if row["symbol"] in symbols:
                company_reports.setdefault(row["symbol"], dict(row))
        for row in db.execute("SELECT a.* FROM report_artifacts a JOIN research_tasks t ON t.task_id=a.task_id WHERE a.report_type IN ('industry','synthesis') AND t.state='completed' ORDER BY a.rowid DESC"):
            report = read_json(root / row["path"])
            scope = report.get("scope") or {}
            if scope.get("industry_id") in industry_ids and scope.get("disclosure_window") == window:
                industry_reports.setdefault((scope["industry_id"],row["report_type"]),dict(row))
        heads = {r['sha256'] for r in [*company_reports.values(), *industry_reports.values()]}
        for row in db.execute("SELECT * FROM publication_jobs WHERE state IN ('complete','archived') ORDER BY updated_at DESC"):
            if row['source_sha256'] not in heads or not row['publication_manifest_path']: continue
            manifest = read_json(root / row['publication_manifest_path'])
            cloud = db.execute("SELECT url,state FROM publication_delivery WHERE publication_id=?",(manifest['publication_id'],)).fetchone()
            publications.append({'scope_id':row['scope_id'],'type':row['publication_type'],
                'state':row['state'],'url':cloud['url'] if cloud and cloud['state']=='verified' else None})
        failures = [dict(r) for r in db.execute("SELECT scope,item_key FROM source_failures WHERE resolved_at IS NULL") if r['scope'] in symbols]
        pending_publications = [dict(r) for r in db.execute("SELECT scope_id,state,error FROM publication_jobs WHERE state NOT IN ('complete','archived','superseded')") if r['scope_id'] in [*symbols,*industry_ids]]
    return {'checked_at':utc_now(),'window':window,'round':round_row,'live_worker_pids':live,
        'expected_companies':len(symbols),'expected_industries':len(industries),
        'company_reports':list(company_reports.values()),'industry_reports':list(industry_reports.values()),
        'publications':publications,'without_company_report':sorted(set(symbols)-set(company_reports)),
        'source_failures':failures,'pending_publications':pending_publications}


def render(data: dict, slot: str) -> str:
    round_row=data['round']; state=round_row.get('state','unknown')
    labels={'active':'研究中','paused_quota':'模型额度暂停','paused_capacity':'模型服务容量暂停',
        'blocked':'存在待处理卡点','yielded':'等待续跑','waiting':'本轮可执行工作已结束，等待后续披露',
        'complete':'本轮已完成','delivery_pending':'等待消息交付','delivery_unknown':'消息交付状态待核实'}
    lines=[f'财报研究每小时心跳｜{slot}（北京时间）',
        f"披露窗口：{data['window']['start']} 至 {data['window']['end_exclusive']}（不含结束日）。",
        f"状态：{labels.get(state,state)}；存活研究工作进程 {len(data['live_worker_pids'])} 个。",
        f"新增公司分析：{len(data['company_reports'])}/{data['expected_companies']} 家；已完成不代表证据完整。",
        f"新增行业增量分析：{sum(r['report_type']=='industry' for r in data['industry_reports'])}/{data['expected_industries']}；季度综合：{sum(r['report_type']=='synthesis' for r in data['industry_reports'])}/{data['expected_industries']}。",
        f"新增范围已校验可读报告：{len(data['publications'])} 份；待发布/审校：{len(data['pending_publications'])} 份。"]
    quota = data.get('quota') or {}
    if quota.get('enabled'):
        if quota.get('remaining_percent') is None:
            lines.append('额度读取失败：已阻止新的研究调用，等待下次核验。')
        else:
            lines.append(f"Codex 剩余额度：{quota['remaining_percent']:g}%；停止阈值：低于 {quota['minimum_remaining_percent']:g}%。")
        if quota.get('pause_required'):
            lines.append('额度保护已触发：不启动新研究调用，已启动调用保存结果后暂停。')
    if data['company_reports']:
        lines.append('已分析：'+'、'.join(f"{r['symbol']}（{r['completeness']}）" for r in data['company_reports'])+'。')
    if data['without_company_report']:
        lines.append('尚无本窗口公司报告：'+'、'.join(data['without_company_report'])+'。已公告未来日期、日期未确认与采集缺口详见本地跟踪表，不以旧财报替代。')
    if state=='paused_quota' and not quota.get('pause_required'):
        lines.append('卡点：既定模型后端返回 usage limit；研究检查点已保留。心跳不调用模型、不反复触发研究，额度恢复后由既定续跑入口继续。')
    elif round_row.get('stop_reason'):
        lines.append('流程原因：'+round_row['stop_reason'])
    if data['source_failures']:
        lines.append('待重试采集：'+'、'.join(sorted({r['scope'] for r in data['source_failures']}))+'。')
    for publication in data['publications'][:5]:
        if publication['url']:lines.append(f"{publication['scope_id']}：{publication['url']}")
    lines.append('本心跳检查状态与额度，不调用模型；低于阈值阻止新研究调用。')
    return '\n'.join(lines)


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-root',default=str(ROOT))
    parser.add_argument('--config',required=True)
    parser.add_argument('--deployment',required=True)
    parser.add_argument('--industry',action='append',required=True)
    parser.add_argument('--execute',action='store_true')
    args=parser.parse_args();root=Path(args.repo_root).resolve()
    config,_=load_config(root,args.config);deployed_path=runtime_path(root,args.deployment)
    deployed=destination(root,deployed_path)
    now=datetime.now(ZoneInfo('Asia/Shanghai'));slot=now.strftime('%Y-%m-%d %H:00')
    folder=runtime_path(root,'runtime/earnings/heartbeat')
    with exclusive_lock(folder/'heartbeat.lock'):
        quota = refresh(root, deployed.get('codex_bin', 'codex'))
        finished=folder/'finished.json'
        if finished.exists() and not quota.get('enabled'):
            print(json.dumps({'status':'skipped','reason':'current supplemental round monitor finished'}));return
        index=folder/(now.strftime('%Y%m%d-%H')+'.json')
        if index.exists():
            record=read_json(index)
        else:
            data=snapshot(root,config,args.industry);data['quota']=quota;body=render(data,slot)
            decision=prepare_notification(root,deployed,day=now.date().isoformat(),body=body,
                report_versions=[],kind='heartbeat',rationale='User explicitly requested hourly research progress reports',should_send=True)
            record={'snapshot':data,'body':body,'decision':str(decision.relative_to(root))}
            atomic_write_json(index,record)
        result=deliver(root,root/record['decision'],deployed_path,execute=args.execute)
        if not quota.get('enabled') and args.execute and result.get('state')=='sent' and record['snapshot']['round'].get('state') in {'complete','waiting'}:
            atomic_write_json(finished,{'finished_at':utc_now(),'round_id':record['snapshot']['round']['round_id'],'decision':record['decision']})
        print(json.dumps({'status':result['status'],'delivery':result,'body':record['body']},ensure_ascii=False))


if __name__=='__main__':main()
