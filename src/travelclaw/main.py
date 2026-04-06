"""
TravelClaw CLI入口 — 启动旅行规划AI团队。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from .logging_config import setup_logging
from .team import TravelTeam


def main():
    parser = argparse.ArgumentParser(description="TravelClaw — AI旅行规划团队")
    parser.add_argument(
        "--config",
        "-c",
        default="config.yaml",
        help="团队配置文件路径 (默认: config.yaml)",
    )
    parser.add_argument(
        "--list-agents",
        action="store_true",
        help="列出团队所有成员",
    )
    parser.add_argument(
        "--async",
        dest="use_async",
        action="store_true",
        help="使用异步并行模式（专家Agent并行执行）",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="启动FastAPI Web服务",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="服务绑定地址 (默认: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="服务端口 (默认: 8000)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="日志级别 (默认: INFO)",
    )
    parser.add_argument(
        "request",
        nargs="*",
        help="旅行规划需求（如：'帮我规划5天东京自由行'）",
    )
    args = parser.parse_args()

    setup_logging(level=args.log_level)

    config_path = Path(args.config)
    if not config_path.exists():
        print(f"错误: 找不到配置文件 {config_path}")
        sys.exit(1)

    if args.serve:
        import uvicorn
        from .api.app import create_app

        app = create_app(config_path=str(config_path))
        uvicorn.run(app, host=args.host, port=args.port)
        return

    team = TravelTeam(config_path)

    if args.list_agents:
        print("\nTravelClaw 旅行规划团队\n")
        for member in team.list_agents():
            marker = "[orchestrator]" if member["type"] == "orchestrator" else "[specialist] "
            domain = f" [{member.get('domain', '')}]" if member.get("domain") else ""
            print(f"  {marker} {member['id']:15s} -- {member['role']}{domain}")
        print()
        return

    if not args.request:
        # 交互模式
        print("\nTravelClaw -- AI旅行规划团队")
        print("=" * 45)
        print("输入你的旅行需求，按Ctrl+C退出\n")
        try:
            while True:
                request = input("你想去哪里？> ").strip()
                if not request:
                    continue
                if args.use_async:
                    response = asyncio.run(team.aplan(request))
                else:
                    response = team.plan(request)
                print("\n" + "=" * 60)
                print(response.plan)
                print(
                    f"\n[plan_id={response.plan_id} | 耗时{response.duration_ms}ms | agents={response.agents_used}]"
                )
                print("=" * 60 + "\n")
        except (KeyboardInterrupt, EOFError):
            print("\n\n再见，祝旅途愉快！")
    else:
        # 命令行模式
        request = " ".join(args.request)
        if args.use_async:
            response = asyncio.run(team.aplan(request))
        else:
            response = team.plan(request)
        print("\n" + "=" * 60)
        print(response.plan)
        print(
            f"\n[plan_id={response.plan_id} | 耗时{response.duration_ms}ms | agents={response.agents_used}]"
        )
        print("=" * 60)


if __name__ == "__main__":
    main()
