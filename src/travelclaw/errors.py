"""
TravelClaw 异常层级。
"""


class TravelClawError(Exception):
    """所有TravelClaw错误的基类"""

    def __init__(self, message: str = "", cause: Exception | None = None):
        super().__init__(message)
        self.cause = cause


class AgentError(TravelClawError):
    """Agent执行任务时发生的错误"""

    def __init__(self, agent_id: str, message: str = "", cause: Exception | None = None):
        super().__init__(f"[{agent_id}] {message}", cause)
        self.agent_id = agent_id


class DecompositionError(TravelClawError):
    """总规划师拆解子任务失败"""
    pass


class DispatchError(TravelClawError):
    """子任务分发过程中的错误"""
    pass


class IntegrationError(TravelClawError):
    """结果整合失败"""
    pass
