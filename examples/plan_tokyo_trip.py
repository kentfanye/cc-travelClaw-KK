"""
示例：使用TravelClaw规划一次东京之旅。

运行方式：
  cd cc-travelClaw-KK
  pip install -e .
  python examples/plan_tokyo_trip.py
"""

import asyncio
from pathlib import Path

from travelclaw.logging_config import setup_logging
from travelclaw.team import TravelTeam


async def main():
    setup_logging(level="INFO")

    # 从项目根目录的config.yaml加载团队
    project_root = Path(__file__).parent.parent
    team = TravelTeam(project_root / "config.yaml")

    # 列出团队成员
    print("团队成员：")
    for m in team.list_agents():
        print(f"  - {m['id']}: {m['role']}")
    print()

    # 用户需求
    request = """
    帮我规划一个5天4晚的东京自由行：
    - 2人出行（情侣）
    - 预算总共2万人民币（不含机票）
    - 喜欢动漫文化、美食探店、逛街购物
    - 想体验一次温泉
    - 不想行程太赶，每天安排2-3个主要景点就好
    """

    print("开始规划旅行（异步并行模式）...\n")

    # 使用异步并行模式 — 专家Agent并行执行
    response = await team.aplan(request)

    print("=" * 60)
    print(response.plan)
    print(f"\n[plan_id={response.plan_id} | 耗时{response.duration_ms}ms | agents={response.agents_used}]")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
