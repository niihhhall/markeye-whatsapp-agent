from langgraph.graph import END
from .state import GraphState

def route_after_load(state: GraphState) -> str:
    if state.get("should_exit"):
        return END
    msg = state.get("message", "").strip().lower()
    sess = state.get("session", {})
    if msg.startswith(("/reset", "#reset")) or sess.get("sim_collecting"):
        return "handle_special"
    return "classify_stage"

def route_after_special(state: GraphState) -> str:
    return END if state.get("should_exit") else "classify_stage"

def route_after_spam(state: GraphState) -> str:
    return END if state.get("should_exit") else "retrieve_knowledge"

def route_after_generate(state: GraphState) -> str:
    return END if state.get("should_exit") else "execute_tools"

def route_after_deliver(state: GraphState) -> str:
    return END if state.get("should_exit") else "persist_session"
