"""Companion fusion layer — the Partner bots re-engineered stdlib-only.

personas:  faithful ports of the original system prompts (companion/Alex/realistic)
mood:      unified mood engine (triggers + decay + sampling params), persistent
web_search: dependency-free DuckDuckGo search + research tools
local_llm:  llama.cpp GGUF provider + model manager for the LLM router
companion: the CompanionAgent (7th agent: chat + tools + memory)
server:    stdlib HTTP server uniting chat + quant (WhatsApp-compatible API)
"""
