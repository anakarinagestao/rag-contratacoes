"""Estado de conversa do cliente Streamlit, sem dependência da interface.

O histórico é deliberadamente efêmero: permanece apenas no
``st.session_state`` e nunca inclui token ou senha. O backend recebe somente a
última análise validada, não o texto completo da conversa.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any
from uuid import uuid4


CHAT_CONVERSATIONS_KEY = "chat_conversations"
CHAT_ACTIVE_KEY = "chat_active_conversation"


def criar_conversa(state: MutableMapping[str, Any]) -> str:
    conversation_id = str(uuid4())
    conversations = state.setdefault(CHAT_CONVERSATIONS_KEY, {})
    conversations[conversation_id] = {
        "id": conversation_id,
        "title": "Nova conversa",
        "messages": [],
        "context": None,
    }
    state[CHAT_ACTIVE_KEY] = conversation_id
    return conversation_id


def inicializar_chat(state: MutableMapping[str, Any]) -> str:
    conversations = state.setdefault(CHAT_CONVERSATIONS_KEY, {})
    active = state.get(CHAT_ACTIVE_KEY)
    if not conversations or active not in conversations:
        return criar_conversa(state)
    return str(active)


def conversa_ativa(state: MutableMapping[str, Any]) -> dict[str, Any]:
    active = inicializar_chat(state)
    return state[CHAT_CONVERSATIONS_KEY][active]


def selecionar_conversa(state: MutableMapping[str, Any], conversation_id: str) -> None:
    if conversation_id not in state.get(CHAT_CONVERSATIONS_KEY, {}):
        raise KeyError("Conversa inexistente nesta sessão.")
    state[CHAT_ACTIVE_KEY] = conversation_id


def registrar_pergunta(conversation: dict[str, Any], question: str) -> None:
    clean = question.strip()
    conversation["messages"].append({"role": "user", "content": clean})
    if conversation["title"] == "Nova conversa":
        conversation["title"] = clean[:52] + ("…" if len(clean) > 52 else "")


def registrar_resposta(
    conversation: dict[str, Any],
    status: int | None,
    payload: dict[str, Any] | str,
) -> None:
    conversation["messages"].append(
        {"role": "assistant", "status": status, "payload": payload}
    )
    if status is not None and status < 400 and isinstance(payload, dict):
        analysis = payload.get("analysis")
        if isinstance(analysis, dict):
            conversation["context"] = analysis


def montar_requisicao(
    conversation: dict[str, Any], question: str, top_k: int
) -> dict[str, Any]:
    body: dict[str, Any] = {"pergunta": question.strip(), "top_k": top_k}
    context = conversation.get("context")
    if isinstance(context, dict):
        body["contexto"] = context
    return body
