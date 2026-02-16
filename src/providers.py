"""Routing logic and request forwarding to model providers."""

import json
import time
from datetime import datetime
from flask import jsonify, Response
import requests
from typing import Dict, Any, Optional

from src.config import (
    logger,
    ROUTER_URL, PRIMARY_URL,
    XAI_API_KEY, XAI_API_URL, XAI_MODEL,
    ROUTER_MODEL, PRIMARY_MODEL,
    ROUTING_SYSTEM_PROMPT, ROUTING_PROMPT,
    ENRICHMENT_SYSTEM_PROMPT,
)
from src.session_logger import SessionLogger


def determine_route(messages: list, session: SessionLogger = None) -> str:
    """
    Use Mini 4B to determine routing via prompt-based classification.
    Routes to: 'router' (Mini 4B), 'primary' (local Nano 30B), or 'xai' (xAI API).

    Args:
        messages: List of message dictionaries
        session: Optional SessionLogger for request tracking

    Returns:
        'router', 'primary', 'xai', or 'enrich'
    """
    if not messages:
        return 'primary'

    # Get the last user message
    last_message = messages[-1].get('content', '')

    # Build routing classification prompt from external template
    routing_prompt = ROUTING_PROMPT.format(query=last_message)

    classify_start = time.time()
    try:
        # Ask Mini 4B router to classify the query
        response = requests.post(
            f"{ROUTER_URL}/v1/chat/completions",
            json={
                "messages": [
                    {"role": "system", "content": f"Today's date is {datetime.now().strftime('%B %d, %Y')}.\n\n{ROUTING_SYSTEM_PROMPT}"},
                    {"role": "user", "content": routing_prompt}
                ],
                "max_tokens": 10,
                "temperature": 0.0  # Deterministic
            },
            timeout=10  # Timeout for routing decision
        )

        classify_ms = (time.time() - classify_start) * 1000

        if response.status_code != 200:
            logger.warning(f"Routing classification returned status {response.status_code}, defaulting to primary")
            if session:
                session.set_route('primary', f'[error: status {response.status_code}]', classify_ms)
            return 'primary'

        result = response.json()
        # Extract decision from response (handle both content and reasoning_content)
        message = result['choices'][0]['message']
        decision = (message.get('content') or message.get('reasoning_content') or '').strip().upper()

        route = 'primary'  # default
        if 'ENRICH' in decision:
            logger.info("Routing to enrichment pipeline: prompt-based classification (ENRICH)")
            route = 'enrich'
        elif 'SIMPLE' in decision:
            logger.info("Routing to router model: prompt-based classification (SIMPLE)")
            route = 'router'
        elif 'MODERATE' in decision:
            logger.info("Routing to primary model: prompt-based classification (MODERATE)")
            route = 'primary'
        elif 'COMPLEX' in decision:
            logger.info("Routing to xAI model: prompt-based classification (COMPLEX)")
            route = 'xai'
        else:
            logger.warning(f"Routing classification unclear: '{decision}', defaulting to primary")

        if session:
            session.set_route(route, decision, classify_ms)
        return route

    except requests.exceptions.Timeout:
        classify_ms = (time.time() - classify_start) * 1000
        logger.warning("Routing classification timeout, defaulting to primary")
        if session:
            session.set_route('primary', '[timeout]', classify_ms)
        return 'primary'
    except Exception as e:
        classify_ms = (time.time() - classify_start) * 1000
        logger.error(f"Error in prompt-based routing: {str(e)}, defaulting to primary")
        if session:
            session.set_route('primary', f'[error: {str(e)}]', classify_ms)
        return 'primary'


def fetch_enrichment_context(messages: list, session: SessionLogger = None) -> Optional[str]:
    """
    Call xAI to retrieve current/real-time context for the user's query.
    Returns the enrichment text, or None if the call fails.
    """
    last_message = messages[-1].get('content', '') if messages else ''

    enrich_messages = [
        {"role": "system", "content": ENRICHMENT_SYSTEM_PROMPT},
        {"role": "user", "content": last_message}
    ]

    if session:
        session.begin_step('enrichment', 'xai', f"{XAI_API_URL}/v1/chat/completions",
                           XAI_MODEL, messages=enrich_messages)

    try:
        response = requests.post(
            f"{XAI_API_URL}/v1/chat/completions",
            json={
                "messages": enrich_messages,
                "model": XAI_MODEL,
                "max_tokens": 1024,
                "temperature": 0.0
            },
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {XAI_API_KEY}'
            },
            timeout=30
        )

        if response.status_code == 200:
            result = response.json()
            message = result['choices'][0]['message']
            context = (message.get('content') or message.get('reasoning_content') or '').strip()
            if context:
                logger.info(f"Enrichment context retrieved ({len(context)} chars)")
                if session:
                    session.end_step(status=200, response_content=context)
                return context

        logger.warning(f"Enrichment call returned status {response.status_code}")
        if session:
            session.end_step(status=response.status_code, error=f'status {response.status_code}')
        return None

    except requests.exceptions.Timeout:
        logger.warning("Enrichment context fetch timed out")
        if session:
            session.end_step(error='timeout')
        return None
    except Exception as e:
        logger.error(f"Error fetching enrichment context: {str(e)}")
        if session:
            session.end_step(error=str(e))
        return None


def get_model_url(route: str) -> str:
    """Get the appropriate model URL based on route."""
    if route == 'router':
        return ROUTER_URL
    elif route == 'xai':
        return XAI_API_URL
    else:
        return PRIMARY_URL


def forward_request(target_url: str, path: str, data: Dict[Any, Any], route: str = None, session: SessionLogger = None) -> Response:
    """
    Forward request to target model with proper error handling.

    Args:
        target_url: Base URL of target model
        path: API path (e.g., '/v1/chat/completions')
        data: Request payload
        route: Route type ('router', 'primary', 'xai')
        session: Optional SessionLogger for request tracking

    Returns:
        Flask Response object
    """
    try:
        url = f"{target_url}{path}"
        logger.info(f"Forwarding request to {url}")

        # Inject current date into messages so all models know the real date
        if 'messages' in data:
            current_date = datetime.now().strftime('%B %d, %Y')
            data['messages'].insert(0, {
                "role": "system",
                "content": f"Today's date is {current_date}."
            })

        # Set up headers
        headers = {'Content-Type': 'application/json'}

        # Override model to match the target backend
        if route == 'xai' and XAI_API_KEY:
            headers['Authorization'] = f'Bearer {XAI_API_KEY}'
            data['model'] = XAI_MODEL
        elif route == 'router':
            data['model'] = ROUTER_MODEL
        else:
            data['model'] = PRIMARY_MODEL

        is_stream = data.get('stream', False)

        # Log the outbound request (exclude internal _route key)
        log_params = {k: v for k, v in data.items() if k not in ('messages', '_route')}
        if session:
            session.begin_step('provider_call', route or 'primary', url, data.get('model'),
                               messages=data.get('messages'), params=log_params)

        # Forward the request
        response = requests.post(
            url,
            json=data,
            headers=headers,
            stream=is_stream,
            timeout=300  # 5 minute timeout for long generations
        )

        if is_stream:
            if session:
                session.end_step(status=response.status_code, response_content='[streamed]')
            # Stream SSE chunks back to the client
            return Response(
                response.iter_content(chunk_size=None),
                status=response.status_code,
                content_type='text/event-stream'
            )

        # Capture response content for logging
        response_body = response.content
        if session:
            try:
                resp_json = json.loads(response_body)
                # Extract the assistant's response text for the log
                resp_text = resp_json.get('choices', [{}])[0].get('message', {}).get('content', '')
                session.end_step(status=response.status_code, response_content=resp_text or response_body.decode('utf-8', errors='replace'))
            except (json.JSONDecodeError, IndexError, KeyError):
                session.end_step(status=response.status_code, response_content=response_body.decode('utf-8', errors='replace'))

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
