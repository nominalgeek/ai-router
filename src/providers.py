"""Routing logic and request forwarding to model providers."""

import json
import re
import time
from flask import jsonify, Response
import requests
from typing import Dict, Any, Optional

from src.config import (
    logger, date_context,
    ROUTER_URL, PRIMARY_URL,
    XAI_API_KEY, XAI_API_URL, XAI_MODEL,
    ROUTER_MODEL, PRIMARY_MODEL,
    PRIMARY_SYSTEM_PROMPT, XAI_SYSTEM_PROMPT,
    ROUTING_SYSTEM_PROMPT, ROUTING_PROMPT,
    ENRICHMENT_SYSTEM_PROMPT,
    XAI_SEARCH_TOOLS,
)
from src.session_logger import SessionLogger


def determine_route(messages: list, session: SessionLogger = None, date_ctx: str = None) -> str:
    """
    Use Orchestrator 8B to determine routing via prompt-based classification.
    Routes to: 'primary' (local Nano 30B), 'xai' (xAI API), or 'enrich' (xAI context → primary).

    Args:
        messages: List of message dictionaries
        session: Optional SessionLogger for request tracking

    Returns:
        'primary', 'xai', or 'enrich'
    """
    if not messages:
        return 'primary'

    # Get the last user message
    last_message = messages[-1].get('content', '')

    # Fast-path: single-message requests that embed their own conversation
    # history are client-generated meta-prompts (follow-up suggestions,
    # title generation, summaries, etc.). They're self-contained and don't
    # need classification or enrichment — route to meta pipeline.
    if len(messages) == 1 and len(last_message) > 300:
        user_msgs = [m for m in messages if m.get('role') == 'user']
        if len(user_msgs) == 1 and any(marker in last_message for marker in (
            'USER:', 'ASSISTANT:', '<chat_history>', '### Task:', '### Guidelines:'
        )):
            # Guard rail: truncate embedded chat history if it would blow
            # the primary model's context (~32K tokens ≈ ~120K chars).
            # Leave ~4K tokens of headroom for system prompt + generation.
            max_chars = 112000  # ~28K tokens
            if len(last_message) > max_chars:
                logger.warning(f"Meta-prompt too long ({len(last_message)} chars), truncating")
                # Try to truncate within <chat_history> tags, keeping recent messages
                start = last_message.find('<chat_history>')
                end = last_message.find('</chat_history>')
                if start >= 0 and end > start:
                    prefix = last_message[:start + len('<chat_history>\n')]
                    suffix = last_message[end:]
                    history = last_message[start + len('<chat_history>\n'):end]
                    # Keep the tail of the history (most recent exchanges)
                    budget = max_chars - len(prefix) - len(suffix)
                    history = history[-budget:]
                    # Snap to the next complete line to avoid mid-message cuts
                    nl = history.find('\n')
                    if nl >= 0:
                        history = history[nl + 1:]
                    messages[-1]['content'] = prefix + history + suffix
                else:
                    # No tags found — just truncate from the front
                    messages[-1]['content'] = last_message[-max_chars:]

            logger.info("Detected meta-prompt, routing to meta pipeline")
            if session:
                session.set_route('meta', 'META', 0)
            return 'meta'

    # Include prior conversation so the classifier can resolve references
    # like "that school" or "it".  Both the classifier and primary now
    # share the same 32K context window, so no truncation is needed.
    context_prefix = ''
    prior = messages[:-1]
    if prior:
        lines = []
        for m in prior:
            role = m.get('role', 'unknown')
            content = m.get('content', '')
            # Strip <details> reasoning tags so the classifier sees
            # the actual answer, not internal chain-of-thought
            content = re.sub(r'<details[^>]*>.*?</details>\s*', '', content, flags=re.DOTALL)
            content = content.strip()
            lines.append(f"{role}: {content}")
        context_prefix = (
            "Recent conversation context (for resolving references):\n"
            + "\n".join(lines)
            + "\n\n"
        )

    # Build routing classification prompt from external template
    routing_prompt = context_prefix + ROUTING_PROMPT.format(
        query=last_message
    )

    classify_messages = [
        {"role": "system", "content": f"{date_ctx or date_context()}\n\n{ROUTING_SYSTEM_PROMPT}"},
        {"role": "user", "content": routing_prompt}
    ]
    classify_params = {"temperature": 0.0, "max_tokens": 1024}
    classify_url = f"{ROUTER_URL}/v1/chat/completions"

    if session:
        session.begin_step('classification', 'router', classify_url, ROUTER_MODEL,
                           messages=classify_messages, params=classify_params)

    classify_start = time.time()
    try:
        # Ask Orchestrator 8B router to classify the query
        response = requests.post(
            classify_url,
            json={
                "messages": classify_messages,
                **classify_params,
            },
            timeout=10  # Timeout for routing decision
        )

        classify_ms = (time.time() - classify_start) * 1000

        if response.status_code != 200:
            logger.warning(f"Routing classification returned status {response.status_code}, defaulting to primary")
            if session:
                session.end_step(status=response.status_code, error=f'status {response.status_code}')
                session.set_route('primary', f'[error: status {response.status_code}]', classify_ms)
            return 'primary'

        result = response.json()
        # Extract decision from response (handle both content and reasoning_content).
        # The Orchestrator 8B wraps its reasoning in <think>...</think> tags.
        # Strip closed blocks first, then any unclosed trailing block (the
        # model ran out of tokens mid-reasoning).  What remains should be
        # just the classification word.
        choice = result['choices'][0]
        finish_reason = choice.get('finish_reason')
        message = choice['message']
        raw = (message.get('content') or message.get('reasoning_content') or '').strip()
        decision = re.sub(r'<think>.*?</think>', '', raw, flags=re.DOTALL | re.IGNORECASE)
        decision = re.sub(r'<think>.*', '', decision, flags=re.DOTALL | re.IGNORECASE)
        decision = decision.strip().upper()

        route = 'primary'  # default
        if 'ENRICH' in decision:
            route = 'enrich'
        elif 'MODERATE' in decision:
            route = 'primary'
        elif 'COMPLEX' in decision:
            route = 'xai'
        else:
            # Catch-all: unrecognized classification (including stale "SIMPLE"
            # from cached responses) defaults to primary with a warning.
            logger.warning(f"Routing classification unclear: '{decision}', defaulting to primary")

        logger.info(f"Classification completed: {decision} -> {route} in {classify_ms:.0f}ms (finish_reason={finish_reason})")

        if session:
            session.end_step(status=response.status_code, response_content=raw, finish_reason=finish_reason)
            session.set_route(route, decision, classify_ms)
        return route

    except requests.exceptions.Timeout:
        classify_ms = (time.time() - classify_start) * 1000
        logger.warning("Routing classification timeout, defaulting to primary")
        if session:
            session.end_step(error='timeout')
            session.set_route('primary', '[timeout]', classify_ms)
        return 'primary'
    except Exception as e:
        classify_ms = (time.time() - classify_start) * 1000
        logger.error(f"Error in prompt-based routing: {str(e)}, defaulting to primary")
        if session:
            session.end_step(error=str(e))
            session.set_route('primary', f'[error: {str(e)}]', classify_ms)
        return 'primary'


def start_speculative_primary(data: dict, date_ctx: str, is_stream: bool):
    """Fire a speculative primary model request (runs in parallel with classification).

    Prepares an independent copy of the request data with system prompt and
    model set for the primary backend, then sends it.  The caller must close
    the returned response if the route turns out to be non-primary.

    Returns:
        (requests.Response, float) — the HTTP response and request start time,
        or (None, 0) if the request fails to start.
    """
    start = time.time()
    try:
        # Independent copy so we don't mutate the caller's data.
        # Shallow-copy each message dict so system prompt injection
        # doesn't affect the original messages list.
        spec_data = dict(data)
        spec_data['messages'] = [dict(m) for m in data['messages']]
        spec_data.pop('max_tokens', None)
        spec_data.pop('_route', None)
        spec_data['model'] = PRIMARY_MODEL
        # Recommended sampling settings for Nemotron Nano reasoning tasks
        spec_data['temperature'] = 1.0
        spec_data['top_p'] = 1.0

        # Inject temporal context + primary system prompt (mirrors forward_request)
        context_line = f"{date_ctx}\n{PRIMARY_SYSTEM_PROMPT}"
        first_system = next((m for m in spec_data['messages'] if m.get('role') == 'system'), None)
        if first_system:
            first_system['content'] = f"{context_line}\n\n{first_system['content']}"
        else:
            spec_data['messages'].insert(0, {"role": "system", "content": context_line})

        response = requests.post(
            f"{PRIMARY_URL}/v1/chat/completions",
            json=spec_data,
            headers={'Content-Type': 'application/json'},
            stream=is_stream,
            timeout=300,
        )
        return response, start
    except Exception as e:
        logger.warning(f"Speculative primary failed to start: {e}")
        return None, 0


def _build_search_tools() -> list:
    """Build the tools list from the XAI_SEARCH_TOOLS config."""
    if not XAI_SEARCH_TOOLS:
        return []
    return [{"type": t.strip()} for t in XAI_SEARCH_TOOLS.split(',') if t.strip()]


def fetch_enrichment_context(messages: list, session: SessionLogger = None, date_ctx: str = None) -> Optional[str]:
    """
    Call xAI /v1/responses to retrieve current/real-time context for the user's query.
    Uses web_search and x_search tools when configured via XAI_SEARCH_TOOLS.
    Returns the enrichment text, or None if the call fails.
    """
    # Pass full conversation history so Grok can resolve references
    # (e.g. "that school") via the prior turns.
    enrich_input = [
        {"role": "system", "content": f"{date_ctx or date_context()}\n\n{ENRICHMENT_SYSTEM_PROMPT}"},
    ]
    for m in messages:
        role = m.get('role', 'user')
        if role in ('user', 'assistant'):
            enrich_input.append({"role": role, "content": m.get('content', '')})

    tools = _build_search_tools()
    enrich_url = f"{XAI_API_URL}/v1/responses"

    request_body = {
        "input": enrich_input,
        "model": XAI_MODEL,
        "max_output_tokens": 1024,
        "temperature": 0.0,
    }
    if tools:
        request_body["tools"] = tools

    if session:
        session.begin_step('enrichment', 'xai', enrich_url, XAI_MODEL,
                           messages=enrich_input,
                           params={k: v for k, v in request_body.items() if k != 'input'})

    enrich_start = time.time()
    try:
        response = requests.post(
            enrich_url,
            json=request_body,
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {XAI_API_KEY}'
            },
            timeout=60
        )

        enrich_ms = (time.time() - enrich_start) * 1000

        if response.status_code == 200:
            result = response.json()
            # Extract text from /v1/responses output format
            context = ''
            for item in result.get('output', []):
                if item.get('type') == 'message':
                    for block in item.get('content', []):
                        if block.get('type') == 'output_text':
                            context += block.get('text', '')
            context = context.strip()
            if context:
                logger.info(f"Enrichment context retrieved: {len(context)} chars in {enrich_ms:.0f}ms")
                if session:
                    session.end_step(status=200, response_content=context)
                return context

        logger.warning(f"Enrichment call failed: status={response.status_code} in {enrich_ms:.0f}ms")
        if session:
            session.end_step(status=response.status_code, error=f'status {response.status_code}')
        return None

    except requests.exceptions.Timeout:
        enrich_ms = (time.time() - enrich_start) * 1000
        logger.warning(f"Enrichment context fetch timed out after {enrich_ms:.0f}ms")
        if session:
            session.end_step(error='timeout')
        return None
    except Exception as e:
        enrich_ms = (time.time() - enrich_start) * 1000
        logger.error(f"Error fetching enrichment context: {str(e)} after {enrich_ms:.0f}ms")
        if session:
            session.end_step(error=str(e))
        return None


def get_model_url(route: str) -> str:
    """Get the appropriate model URL based on route."""
    if route == 'xai':
        return XAI_API_URL
    else:
        return PRIMARY_URL


def forward_request(target_url: str, path: str, data: Dict[Any, Any], route: str = None, session: SessionLogger = None, date_ctx: str = None) -> Response:
    """
    Forward request to target model with proper error handling.

    Args:
        target_url: Base URL of target model
        path: API path (e.g., '/v1/chat/completions')
        data: Request payload
        route: Route type ('primary', 'xai')
        session: Optional SessionLogger for request tracking

    Returns:
        Flask Response object
    """
    try:
        url = f"{target_url}{path}"
        logger.info(f"Forwarding request to {url}")

        # Inject temporal context + route-specific system prompt into the
        # first system message, or prepend one.  Each route has its own
        # behavioral prompt: primary gets conciseness + reasoning guidance,
        # xAI gets conciseness tuned for a cloud model.
        if 'messages' in data:
            system_prompt = XAI_SYSTEM_PROMPT if route == 'xai' else PRIMARY_SYSTEM_PROMPT
            context_line = f"{date_ctx or date_context()}\n{system_prompt}"
            first_system = next((m for m in data['messages'] if m.get('role') == 'system'), None)
            if first_system:
                first_system['content'] = f"{context_line}\n\n{first_system['content']}"
            else:
                data['messages'].insert(0, {"role": "system", "content": context_line})

        # Set up headers
        headers = {'Content-Type': 'application/json'}

        # Override model to match the target backend
        if route == 'xai' and XAI_API_KEY:
            headers['Authorization'] = f'Bearer {XAI_API_KEY}'
            data['model'] = XAI_MODEL
        else:
            data['model'] = PRIMARY_MODEL
            # Recommended sampling settings for Nemotron Nano reasoning tasks
            data['temperature'] = 1.0
            data['top_p'] = 1.0

        is_stream = data.get('stream', False)

        # Log the outbound request (exclude internal _route key)
        log_params = {k: v for k, v in data.items() if k not in ('messages', '_route')}
        if session:
            session.begin_step('provider_call', route or 'primary', url, data.get('model'),
                               messages=data.get('messages'), params=log_params)

        # Forward the request
        forward_start = time.time()
        response = requests.post(
            url,
            json=data,
            headers=headers,
            stream=is_stream,
            timeout=300  # 5 minute timeout for long generations
        )

        if is_stream:
            forward_ms = (time.time() - forward_start) * 1000
            if session:
                session.end_step(status=response.status_code, response_content='[streamed]')

            # Wrap the SSE iterator to log TTFT (time from request start
            # to first data chunk reaching the client) without buffering.
            def _stream_with_ttft(raw_iter, route_name, start_time, connect_ms):
                first_chunk = True
                for chunk in raw_iter:
                    if first_chunk:
                        ttft_ms = (time.time() - start_time) * 1000
                        logger.info(
                            f"Provider response: {route_name} status={response.status_code}"
                            f" connect_ms={connect_ms:.0f} ttft_ms={ttft_ms:.0f} stream=true"
                        )
                        first_chunk = False
                    yield chunk

            return Response(
                _stream_with_ttft(response.iter_content(chunk_size=None),
                                  route or 'primary', forward_start, forward_ms),
                status=response.status_code,
                content_type='text/event-stream'
            )

        forward_ms = (time.time() - forward_start) * 1000

        # Capture response content for logging
        response_body = response.content
        finish_reason = None
        if session:
            try:
                resp_json = json.loads(response_body)
                # Extract the assistant's response text for the log.
                # Reasoning models may put all output in reasoning_content
                # with content=null (especially when max_tokens is tight).
                choice = resp_json.get('choices', [{}])[0]
                finish_reason = choice.get('finish_reason')
                msg = choice.get('message', {})
                resp_text = msg.get('content') or msg.get('reasoning_content') or ''
                session.end_step(status=response.status_code, response_content=resp_text or response_body.decode('utf-8', errors='replace'), finish_reason=finish_reason)
            except (json.JSONDecodeError, IndexError, KeyError):
                session.end_step(status=response.status_code, response_content=response_body.decode('utf-8', errors='replace'))

        logger.info(f"Provider response: {route or 'primary'} status={response.status_code} duration_ms={forward_ms:.0f} finish_reason={finish_reason} stream=false")

        # Return buffered response with same status code
        return Response(
            response_body,
            status=response.status_code,
            content_type=response.headers.get('Content-Type', 'application/json')
        )

    except requests.exceptions.Timeout:
        logger.error(f"Request timeout to {target_url}")
        if session:
            session.end_step(error='timeout')
        return jsonify({
            'error': 'Request timeout',
            'message': 'The model took too long to respond'
        }), 504

    except requests.exceptions.ConnectionError:
        logger.error(f"Connection error to {target_url}")
        if session:
            session.end_step(error='connection_error')
        return jsonify({
            'error': 'Service unavailable',
            'message': f'Cannot connect to model service'
        }), 503

    except Exception as e:
        logger.error(f"Error forwarding request: {str(e)}")
        if session:
            session.end_step(error=str(e))
        return jsonify({
            'error': 'Internal error',
            'message': str(e)
        }), 500
