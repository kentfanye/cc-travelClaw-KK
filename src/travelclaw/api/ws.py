"""
WebSocket 端点 — 支持多轮咨询对话 + 确认后行程规划。

协议：
  客户端发送:
    {"type": "message", "content": "用户消息"}    — 咨询对话
    {"type": "confirm"}                          — 确认需求，开始规划
    {"type": "plan", "request": "需求文本"}       — 直接规划（跳过咨询）

  服务端发送:
    {"type": "reply", "content": "规划师回复"}     — 咨询回复
    {"type": "checklist", "data": {...}}          — 需求确认清单
    {"type": "plan_start", ...}                   — 规划进度事件
    {"type": "plan_complete", ...}                — 规划完成
    {"type": "plan_error", ...}                   — 规划错误
    {"type": "error", "data": "错误信息"}          — 通用错误
"""

from __future__ import annotations

import json
import re
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

ws_router = APIRouter()


def _extract_checklist(text: str) -> dict | None:
    """尝试从规划师回复中提取需求确认清单JSON。"""
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(1))
            if data.get("type") == "checklist" and data.get("destination"):
                return data
        except (json.JSONDecodeError, AttributeError):
            pass
    return None


@ws_router.websocket("/plan/stream")
async def plan_stream(websocket: WebSocket):
    """
    统一WebSocket端点 — 支持咨询对话和行程规划。
    """
    await websocket.accept()
    session_id = uuid.uuid4().hex[:12]
    team = websocket.app.state.team
    last_checklist = None

    try:
        while True:
            raw = await websocket.receive_json()
            msg_type = raw.get("type", "message")

            if msg_type == "message":
                # ── 咨询对话 ──
                content = raw.get("content", "").strip()
                if not content:
                    await websocket.send_json({"type": "error", "data": "消息不能为空"})
                    continue

                try:
                    reply = await team.aconsult(session_id, content)
                except Exception as e:
                    await websocket.send_json({"type": "error", "data": f"对话失败: {e}"})
                    continue

                # 检查回复中是否包含需求确认清单
                checklist = _extract_checklist(reply)
                if checklist:
                    last_checklist = checklist
                    # 发送清单（去掉JSON代码块，保留自然语言部分）
                    clean_reply = re.sub(
                        r"```(?:json)?\s*\{.*?\}\s*```", "", reply, flags=re.DOTALL
                    ).strip()
                    await websocket.send_json({
                        "type": "checklist",
                        "data": checklist,
                        "message": clean_reply,
                    })
                else:
                    await websocket.send_json({"type": "reply", "content": reply})

            elif msg_type == "confirm":
                # ── 确认需求，开始规划 ──
                if not last_checklist:
                    await websocket.send_json({
                        "type": "error",
                        "data": "还没有生成需求清单，请先和规划师沟通需求",
                    })
                    continue

                # 用清单summary作为规划指令
                plan_request = last_checklist.get("summary", "")
                if not plan_request:
                    plan_request = (
                        f"目的地：{last_checklist.get('destination', '')}，"
                        f"{last_checklist.get('days', '')}天，"
                        f"预算：{last_checklist.get('budget', '')}，"
                        f"出行人：{last_checklist.get('travelers', '')}，"
                        f"住宿：{last_checklist.get('accommodation', '')}，"
                        f"饮食：{last_checklist.get('food_preference', '')}，"
                        f"交通：{last_checklist.get('transport', '')}，"
                        f"兴趣：{last_checklist.get('interests', '')}，"
                        f"特殊需求：{last_checklist.get('special_needs', '')}"
                    )

                # 流式推送规划进度
                async for event in team.aplan_stream(plan_request):
                    event_dict = event.model_dump(mode="json")
                    await websocket.send_json(event_dict)

                # 规划完成后清理会话
                team.clear_session(session_id)
                break

            elif msg_type == "plan":
                # ── 直接规划（跳过咨询，向后兼容） ──
                request_text = raw.get("request", "").strip()
                if not request_text:
                    await websocket.send_json({"type": "error", "data": "缺少request字段"})
                    continue

                async for event in team.aplan_stream(request_text):
                    event_dict = event.model_dump(mode="json")
                    await websocket.send_json(event_dict)
                break

        await websocket.close()

    except WebSocketDisconnect:
        team.clear_session(session_id)
    except Exception as e:
        try:
            await websocket.send_json({"type": "error", "data": str(e)})
            await websocket.close()
        except Exception:
            pass
        team.clear_session(session_id)
