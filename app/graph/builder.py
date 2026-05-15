from langgraph.graph import StateGraph, END
from .state import GraphState
from .nodes import (
    load_context, handle_special, classify_stage_node, check_spam_node,
    retrieve_knowledge_node, generate_response_node, execute_tools_node,
    deliver_response_node, persist_session_node
)
from .edges import (
    route_after_load, route_after_special, route_after_spam,
    route_after_generate, route_after_deliver
)

def _build_graph():
    graph = StateGraph(GraphState)

    graph.add_node("load_context",       load_context)
    graph.add_node("handle_special",     handle_special)
    graph.add_node("classify_stage",     classify_stage_node)
    graph.add_node("check_spam",         check_spam_node)
    graph.add_node("retrieve_knowledge", retrieve_knowledge_node)
    graph.add_node("generate_response",  generate_response_node)
    graph.add_node("execute_tools",      execute_tools_node)
    graph.add_node("deliver_response",   deliver_response_node)
    graph.add_node("persist_session",    persist_session_node)

    graph.set_entry_point("load_context")

    graph.add_conditional_edges(
        "load_context", route_after_load,
        {END: END, "handle_special": "handle_special", "classify_stage": "classify_stage"},
    )
    graph.add_conditional_edges(
        "handle_special", route_after_special,
        {END: END, "classify_stage": "classify_stage"},
    )
    graph.add_edge("classify_stage",     "check_spam")
    graph.add_conditional_edges(
        "check_spam", route_after_spam,
        {END: END, "retrieve_knowledge": "retrieve_knowledge"},
    )
    graph.add_edge("retrieve_knowledge", "generate_response")
    graph.add_conditional_edges(
        "generate_response", route_after_generate,
        {END: END, "execute_tools": "execute_tools"},
    )
    graph.add_edge("execute_tools",      "deliver_response")
    graph.add_conditional_edges(
        "deliver_response", route_after_deliver,
        {END: END, "persist_session": "persist_session"},
    )
    graph.add_edge("persist_session", END)

    return graph.compile()

# Compiled workflow — imported by conversation.py
workflow = _build_graph()
