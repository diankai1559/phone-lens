"""Command line interface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .entities import resolve
from .pipeline import run
from .sources import probe_all
from .bilibili import BiliClient
from .catalog import catalog as get_catalog
from .chat import Conversation, load_result, repl
from .config import load_llm


def _ask(resolution) -> list:
    print(f"机型存在多个版本：{resolution.variants}")
    print("输入序号选择（可多选，逗号分隔），直接回车=全部：")
    for index, variant in enumerate(resolution.variants, start=1):
        print(f"  {index}. {resolution.model_name(variant)}")
    raw = input("> ").strip()
    if not raw:
        return list(resolution.variants)
    picked = []
    for token in raw.replace("，", ",").split(","):
        if token.strip().isdigit() and 1 <= int(token) <= len(resolution.variants):
            picked.append(resolution.variants[int(token) - 1])
    return picked or list(resolution.variants)


def cmd_report(args) -> int:
    result = run(
        args.query,
        top=args.top,
        use_llm=not args.no_llm,
        allow_asr=not args.no_asr,
        assume_family=args.family,
        ask=None if args.family or args.yes else _ask,
    )
    if args.out:
        Path(args.out).write_text(result.report, encoding="utf-8")
        print(f"saved: {args.out}")
    else:
        print(result.report)

    print("", file=sys.stderr)
    print(f"claims={len(result.claims)} conflicts={len(result.conflicts)}", file=sys.stderr)
    if not result.claims:
        print("提示：没有取到正文。先跑 `phonelens probe`；无 B 站登录态时需配置 SILICONFLOW_API_KEY 走 ASR。", file=sys.stderr)
    for check in result.verification.checks:
        mark = "PASS" if check.ok else "FAIL"
        print(f"  [{mark}] {check.name}: {check.detail}", file=sys.stderr)
    if args.json:
        payload = {
            "resolution": result.resolution.__dict__,
            "claims": [c.to_dict() for c in result.claims],
            "conflicts": [c.to_dict() for c in result.conflicts],
            "verification": result.verification.to_dict(),
        }
        Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"json: {args.json}", file=sys.stderr)
    return 0 if result.verification.ok else 1


def cmd_agent(args) -> int:
    from .agent import ReportAgent

    agent = ReportAgent(args.query, top=args.top, allow_asr=not args.no_asr)
    result = agent.run()
    if args.out:
        Path(args.out).write_text(result.report, encoding="utf-8")
        print(f"saved: {args.out}")
    else:
        print(result.report)

    print("", file=sys.stderr)
    for step in result.trace:
        print(f"  [{step.action}] {step.observation}", file=sys.stderr)
    if not result.claims:
        print("提示：没有取到正文。先跑 `phonelens probe`；无 B 站登录态时需配置 SILICONFLOW_API_KEY 走 ASR。", file=sys.stderr)
    for check in result.verification.checks:
        print(f"  [{'PASS' if check.ok else 'FAIL'}] {check.name}: {check.detail}", file=sys.stderr)
    if args.json:
        payload = {
            "resolution": result.resolution.__dict__,
            "claims": [c.to_dict() for c in result.claims],
            "conflicts": [c.to_dict() for c in result.conflicts],
            "trace": [s.__dict__ for s in result.trace],
            "verification": result.verification.to_dict(),
        }
        Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result.verification.ok else 1


def cmd_probe(_args) -> int:
    statuses = probe_all(BiliClient())
    for status in statuses:
        print(f"{status.name:<18} {status.status:<10} {status.detail}")
    return 0


def cmd_chat(args) -> int:
    llm = load_llm()
    if not llm.enabled:
        print("对话需要 LLM key：设置 DEEPSEEK_API_KEY 或 SILICONFLOW_API_KEY", file=sys.stderr)
        return 2

    if args.from_json:
        family, claims, conflicts = load_result(args.from_json)
    else:
        if not args.query:
            print("请提供机型，或使用 --from-json", file=sys.stderr)
            return 2
        result = run(
            args.query,
            top=args.top,
            use_llm=True,
            allow_asr=not args.no_asr,
            assume_family=args.family,
            ask=None if args.family or args.yes else _ask,
        )
        if args.json:
            Path(args.json).write_text(
                json.dumps(
                    {
                        "resolution": result.resolution.__dict__,
                        "claims": [c.to_dict() for c in result.claims],
                        "conflicts": [c.to_dict() for c in result.conflicts],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        family, claims, conflicts = result.resolution.label, result.claims, result.conflicts

    conversation = Conversation(llm, claims, conflicts, family)
    if args.session and Path(args.session).exists():
        conversation.load_history(args.session)

    if args.ask:
        answer = conversation.ask(args.ask)
        print(answer.text)
        return 0 if answer.grounded else 1

    repl(conversation, args.session)
    return 0


def cmd_serve(args) -> int:
    from .server import serve

    serve(host=args.host, port=args.port)
    return 0


def cmd_catalog(args) -> int:
    cat = get_catalog()
    if args.action == "add":
        if not (args.name and args.brand):
            print("add 需要 --name 与 --brand", file=sys.stderr)
            return 2
        aliases = [a.strip() for a in (args.aliases or "").split(",") if a.strip()]
        variants = [v.strip() for v in (args.variants or "").split(",") if v.strip()]
        family = cat.add(args.name, args.brand, aliases or [args.name], variants)
        print(f"added: {family.name} aliases={family.aliases} variants={family.variants}")
        return 0
    for name, family in sorted(cat.families.items()):
        print(f"{name:<24} {family.brand:<8} variants={','.join(family.variants) or '-'}")
    return 0


def cmd_resolve(args) -> int:
    res = resolve(args.query)
    print(json.dumps(res.__dict__, ensure_ascii=False, indent=2))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="phonelens", description="多源可溯源手机评测报告生成器")
    sub = parser.add_subparsers(dest="command", required=True)

    report = sub.add_parser("report", help="生成报告")
    report.add_argument("query", help="机型，如 redmik100 / 红米K100 Pro Max")
    report.add_argument("--top", type=int, default=5, help="最多采集几个视频")
    report.add_argument("--out", help="输出 markdown 路径")
    report.add_argument("--json", help="输出结构化 claim JSON")
    report.add_argument("--no-llm", action="store_true", help="不用 LLM，走启发式抽取")
    report.add_argument("--no-asr", action="store_true", help="禁用 ASR 兜底")
    report.add_argument("--family", action="store_true", help="不提问，直接覆盖整个系列")
    report.add_argument("--yes", action="store_true", help="非交互模式")
    report.set_defaults(func=cmd_report)

    probe = sub.add_parser("probe", help="检测各信源可用性")
    probe.set_defaults(func=cmd_probe)

    agent = sub.add_parser("agent", help="Agent 模式：自己决定补什么证据，输出购买参考")
    agent.add_argument("query")
    agent.add_argument("--top", type=int, default=4, help="最多采集几个视频")
    agent.add_argument("--out", help="输出 markdown")
    agent.add_argument("--json", help="输出结构化结果（含决策日志）")
    agent.add_argument("--no-asr", action="store_true")
    agent.set_defaults(func=cmd_agent)

    resolver = sub.add_parser("resolve", help="查看机型对齐结果")
    resolver.add_argument("query")
    resolver.set_defaults(func=cmd_resolve)

    chat = sub.add_parser("chat", help="基于已生成证据包的多轮对话")
    chat.add_argument("query", nargs="?")
    chat.add_argument("--from-json", dest="from_json", help="从 report --json 的结果进入对话")
    chat.add_argument("--ask", help="只问一个问题后退出")
    chat.add_argument("--session", help="会话文件，用于续聊与保存")
    chat.add_argument("--json", help="把本次抽取结果另存为 json")
    chat.add_argument("--top", type=int, default=5)
    chat.add_argument("--no-asr", action="store_true")
    chat.add_argument("--family", action="store_true")
    chat.add_argument("--yes", action="store_true")
    chat.set_defaults(func=cmd_chat)

    cat = sub.add_parser("catalog", help="查看或新增机型（数据文件驱动）")
    cat.add_argument("action", nargs="?", default="list", choices=["list", "add"])
    cat.add_argument("--name")
    cat.add_argument("--brand")
    cat.add_argument("--aliases", help="逗号分隔")
    cat.add_argument("--variants", help="逗号分隔")
    cat.set_defaults(func=cmd_catalog)

    serve = sub.add_parser("serve", help="启动本地网页界面")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
