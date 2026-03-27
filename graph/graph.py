"""
Alpha Agents LangGraph 오케스트레이션

흐름:
  analysis → strategy → risk → [execute | skip] → END
"""
from langgraph.graph import StateGraph, END
from graph.state import AgentState
from agents.analysis_agent.technical import get_technical_signals
from agents.strategy_agent.xgb_model import predict
from agents.risk_agent.risk import check_risk
from agents.execution_agent.paper_trader import execute_paper


# ── 노드 함수들 ─────────────────────────────────────────────────────────────

async def analysis_node(state: AgentState) -> AgentState:
    signals = await get_technical_signals(state["symbol"])
    return {**state, "signals": signals}


async def strategy_node(state: AgentState) -> AgentState:
    result = predict(state["symbol"], state["signals"])
    return {
        **state,
        "action":     result["action"],
        "confidence": result["confidence"],
        "proba":      result["proba"],
    }


async def risk_node(state: AgentState) -> AgentState:
    return await check_risk(state)


async def execution_node(state: AgentState) -> AgentState:
    return await execute_paper(state)


# ── 라우팅 ───────────────────────────────────────────────────────────────────

def route_after_risk(state: AgentState) -> str:
    return "execute" if state["approved"] else "skip"


async def skip_node(state: AgentState) -> AgentState:
    return state


# ── 그래프 빌드 ──────────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    g = StateGraph(AgentState)

    g.add_node("analysis",  analysis_node)
    g.add_node("strategy",  strategy_node)
    g.add_node("risk",      risk_node)
    g.add_node("execute",   execution_node)
    g.add_node("skip",      skip_node)

    g.set_entry_point("analysis")
    g.add_edge("analysis", "strategy")
    g.add_edge("strategy", "risk")
    g.add_conditional_edges("risk", route_after_risk, {"execute": "execute", "skip": "skip"})
    g.add_edge("execute", END)
    g.add_edge("skip",    END)

    return g.compile()


# 싱글턴
_graph = None

def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph
