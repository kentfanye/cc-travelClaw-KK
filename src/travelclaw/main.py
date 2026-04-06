"""
TravelClaw CLI入口 — 启动旅行规划AI团队。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .team import TravelTeam


def main():
    parser = argparse.ArgumentParser(
        description="TravelClaw — AI旅行规划团队"
    )
    parser.add_argument(
        "--config", "-c",
        default="config.yaml",
        help="团队配置文件路径 (默认: config.yaml)",
    )
    parser.add_argument(
        "--list-agents",
        action="store_true",
        help="列出团队所有成员",
    )
    parser.add_argument(
        "request",
        nargs="*",
        help="旅行规划需求（如：'帮我规划5天东京自由行'）",
    )
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        print(f"错误: 找不到配置文件 {config_path}")
        sys.exit(1)

    team = TravelTeam(config_path)

    if args.list_agents:
        print("\n🏢 TravelClaw 旅行规划团队\n")
        for member in team.list_agents():
            marker = "👑" if member["type"] == "orchestrator" else "🎯"
            domain = f" [{member.get('domain', '')}]" if member.get("domain") else ""
            print(f"  {marker} {member['id']:15s} — {member['role']}{domain}")
        print()
        return

    if not args.request:
        # 交互模式
        print("\n🦞 TravelClaw — AI旅行规划团队")
        print("=" * 45)
        print("输入你的旅行需求，按Ctrl+C退出\n")
        try:
            while True:
                request = input("🗺️  你想去哪里？> ").strip()
                if not request:
                    continue
                result = team.plan(request)
                print("\n" + "=" * 60)
                print(result)
                print("=" * 60 + "\n")
        except (KeyboardInterrupt, EOFError):
            print("\n\n再见，祝旅途愉快！👋")
    else:
        # 命令行模式
        request = " ".join(args.request)
        result = team.plan(request)
        print("\n" + "=" * 60)
        print(result)
        print("=" * 60)


if __name__ == "__main__":
    main()
