"""
WebSocket 流式进度端点。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

ws_router = APIRouter()


@ws_router.websocket("/plan/stream")
async def plan_stream(websocket: WebSocket):
    """
    WebSocket流式规划。

    协议：
    1. 客户端连接后发送JSON: {"request": "旅行需求文本"}
    2. 服务端逐步推送PlanEvent JSON事件
    3. 最后一个事件type为 "plan_complete" 或 "plan_error"
    """
    await websocket.accept()

    try:
        # 接收客户端的规划请求
        data = await websocket.receive_json()
        user_request = data.get("request", "")
        if not user_request:
            await websocket.send_json({"type": "error", "data": "缺少request字段"})
            await websocket.close()
            return

        team = websocket.app.state.team

        # 流式推送进度事件
        async for event in team.aplan_stream(user_request):
            event_dict = event.model_dump(mode="json")
            await websocket.send_json(event_dict)

        await websocket.close()

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_json({"type": "error", "data": str(e)})
            await websocket.close()
        except Exception:
            pass
