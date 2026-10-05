import os
import logging
from typing import Dict, TypedDict
try:
    from langgraph.graph import StateGraph, END
    from langchain_google_genai import ChatGoogleGenerativeAI
    from langchain_core.messages import HumanMessage, SystemMessage
    LANGGRAPH_AVAILABLE = True
except ImportError:
    LANGGRAPH_AVAILABLE = False

from .rag_store import get_rag_context

logger = logging.getLogger(__name__)

# Define the State for LangGraph
class GraphState(TypedDict):
    error_query: str
    file_type: str
    rag_context: str
    final_analysis: str
    image_bytes: bytes

def retrieve_node(state: GraphState) -> Dict:
    """Node 1: Retrieve context from RAG Store"""
    error = state["error_query"]
    context = get_rag_context(error)
    return {"rag_context": context}

def reason_node(state: GraphState) -> Dict:
    """Node 2: Use LLM to reason about the error and propose a solution"""
    api_key = os.environ.get("GEMINI_API_KEY", "")
    
    system_prompt = (
        "You are an expert AI product analyst. "
        "A user has uploaded an image of a product or a code snippet. You must analyze it and output exactly in this format:\n\n"
        "**[LANGGRAPH AI REASONING]**\n\n"
        "**1. Product Name:**\n"
        "> [Name of the product or File Type]\n\n"
        "**2. Damage Percentage:**\n"
        "> [Estimated % of damage or severity]\n\n"
        "**3. What is Damaged (Damage Type):**\n"
        "> [Describe exactly what is broken/damaged in the image or code]\n\n"
        "**4. Reasons for Damage:**\n"
        "> [Provide realistic physical or environmental reasons why this damage occurred based on what you see]\n\n"
        "**5. Recommended Solution:**\n"
        "> [Steps to fix or replace]"
    )
    
    user_prompt = (
        f"File Type: {state.get('file_type', 'Unknown')}\n"
        f"Historical RAG Context: {state.get('rag_context', '')}\n\n"
        "Please provide the detailed breakdown by visually analyzing the uploaded image (if provided) as requested."
    )
    
    if not api_key:
        # Fallback Mock if LLM isn't configured
        logger.warning("LLM API key not found. Using Mock Reasoning.")
        
        is_code = "Code" in state.get('file_type', '')
        if is_code:
            prod_name = "Python Script / System Log"
            severity = "Critical"
            dmg_type = "Syntax Error / Data Corruption"
            reasons = "The data stream was interrupted during transmission or suffered sector-level bit rot."
        else:
            eq = state.get('error_query', '')
            prod_name = eq.split("|")[0].replace("Product:", "").strip() if "|" in eq else "Hardware Component"
            dmg_type = eq.split("|")[1].replace("Damage:", "").strip() if "|" in eq else "Physical Damage"
            severity = eq.split("|")[2].replace("Severity:", "").strip() if "|" in eq else "High"
            reasons = f"The {prod_name} suffered from {dmg_type.lower()} likely due to physical impact, excessive stress, or wear and tear."
        
        mock_response = (
            f"**[LANGGRAPH AI REASONING]**\n\n"
            f"**1. Product Name:**\n> {prod_name}\n\n"
            f"**2. Damage Percentage:**\n> {severity}\n\n"
            f"**3. What is Damaged (Damage Type):**\n> {dmg_type}\n\n"
            f"**4. Reasons for Damage:**\n> {reasons}\n\n"
            f"**5. Recommended Solution:**\n> Proceed with component replacement or schedule an immediate repair.\n\n"
            f"*(Note: Set GEMINI_API_KEY in .env for true image analysis)*"
        )
        return {"final_analysis": mock_response}

    try:
        import google.generativeai as genai
        import io
        from PIL import Image

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        prompt_parts = [system_prompt, "\n\n", user_prompt]
        
        # If we have an image, append it to the vision prompt
        if state.get("image_bytes"):
            img = Image.open(io.BytesIO(state["image_bytes"]))
            prompt_parts.append(img)

        response = model.generate_content(prompt_parts)
        return {"final_analysis": response.text}
        
    except Exception as e:
        logger.error(f"LLM Generation failed: {e}")
        return {"final_analysis": f"AI Engine Error: {str(e)}\n\nFallback Solution: Check the RAG Context -> {state.get('rag_context', '')}"}

def build_graph():
    if not LANGGRAPH_AVAILABLE:
        return None
    
    workflow = StateGraph(GraphState)
    workflow.add_node("retrieve", retrieve_node)
    workflow.add_node("reason", reason_node)
    
    workflow.set_entry_point("retrieve")
    workflow.add_edge("retrieve", "reason")
    workflow.add_edge("reason", END)
    
    return workflow.compile()

# Singleton graph application
app_graph = build_graph()

def run_ai_analysis(error_query: str, file_type: str, image_bytes: bytes = None) -> str:
    """
    Executes the LangGraph AI workflow to analyze an error.
    """
    initial_state = {
        "error_query": error_query,
        "file_type": file_type,
        "rag_context": "",
        "final_analysis": "",
        "image_bytes": image_bytes
    }

    if app_graph is None:
        # Fallback if LangGraph couldn't be built
        initial_state.update(retrieve_node(initial_state))
        initial_state.update(reason_node(initial_state))
        return initial_state["final_analysis"]
        
    final_state = app_graph.invoke(initial_state)
    return final_state["final_analysis"]

