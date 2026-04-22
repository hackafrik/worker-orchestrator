# Capability Matrix

This document maps worker categories to adapter implementations and model/provider capabilities.

| Category | Adapter | Local/Remote | Streaming | Token Cost | Concurrent | Notes |
|----------|---------|--------------|-----------|------------|------------|-------|
| **CLI** | `adapter_cli.py` | Local | No (stdout capture) | Worker-configured | Up to 6 via process caps | Best for opencode, codex, claude-code, aider |
| **HTTP_API** | `adapter_http.py` | Both | SSE, NDJSON, blocking | Per-model pricing | Provider-dependent | ollama (local), vLLM (local), OpenAI/Anthropic/Groq (remote) |
| **PYTHON_SCRIPT** | `adapter_python.py` | Local | No | 0 | Host-limited | Custom inference, data pipelines |
| **DOCKER** | `adapter_docker.py` | Both | Via container stdout | 0 (local infra) | Host-limited | Isolated inference, GPU containers |

## Detected CLI Workers

| Tool | Binary | Detect Command | Version Flag |
|------|--------|----------------|--------------|
| OpenCode | `opencode` | `which opencode` | `--version` |
| Codex | `codex` | `which codex` | `--version` |
| Claude Code | `claude` | `which claude` | `--version` |
| Gemini CLI | `gemini` | `which gemini` | `--version` |
| Continue | `cn` | `which cn` | `--version` |
| Cline | `cline` | `which cline` | `--version` |
| Cursor Agent | `agent` | `which agent` | `--version` |
| Aider | `aider` | `which aider` | `--version` |
| Goose | `goose` | `which goose` | `--version` |

## HTTP API Endpoints

| Service | Base URL | Endpoint | Protocol |
|---------|----------|----------|----------|
| ollama | `http://localhost:11434` | `/api/generate`, `/api/chat` | SSE + JSON |
| vLLM | `http://localhost:8000` | `/v1/completions`, `/v1/chat/completions` | SSE (OpenAI-compatible) |
| OpenAI | `https://api.openai.com/v1` | `/chat/completions` | SSE |
| Anthropic | `https://api.anthropic.com/v1` | `/messages` | SSE |
| Groq | `https://api.groq.com/openai/v1` | `/chat/completions` | SSE |
| Gemini | `https://generativelanguage.googleapis.com/v1beta` | `/models/...:generateContent` | blocking JSON |

## Cost Rates (USD per 1M tokens)

| Provider:Model | Input | Output |
|----------------|-------|--------|
| openai:gpt-4.1 | 2.00 | 8.00 |
| openai:gpt-4o | 2.50 | 10.00 |
| openai:gpt-4o-mini | 0.15 | 0.60 |
| anthropic:claude-sonnet-4 | 3.00 | 15.00 |
| anthropic:claude-opus-4 | 15.00 | 75.00 |
| deepseek:deepseek-chat | 0.27 | 1.10 |
| google:gemini-2.5-pro | 1.25 | 10.00 |
| groq:llama-4-scout | 0.13 | 0.34 |
| local:* | 0.00 | 0.00 |

Rates are best-effort. Actual API costs may vary by region and provider updates.
