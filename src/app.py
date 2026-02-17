"""Flask application and route handlers."""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, request, jsonify, Response
import requests

from src.config import (
    logger, date_context,
    PRIMARY_URL, PRIMARY_MODEL,
    XAI_API_KEY, XAI_API_URL,
    ROUTER_URL,
    VIRTUAL_MODEL,
    ENRICHMENT_INJECTION_PROMPT,
    META_SYSTEM_PROMPT,
    XAI_MIN_MAX_TOKENS,
)
from src.session_logger import SessionLogger
from src.providers import (
    determine_route,
    fetch_enrichment_context,
    get_model_url,
    forward_request,
    start_speculative_primary,
)

app = Flask(__name__)

# Requests slower than this threshold (ms) get a WARNING-level log line.
# Per-route: primary routes should be fast; xai/enrich are inherently slower.
SLOW_REQUEST_THRESHOLDS = {
    'primary': 5000,
    'meta': 5000,
    'xai': 30000,
    'enrich': 60000,
}


def _log_request_summary(session):
    """Emit a structured summary line for the completed request.
    Fires a slow-request warning if total_ms exceeds the route's threshold.
    Called before save(), so compute total_ms from the session start time."""
    d = session.data
    total_ms = round((time.time() - session.start_time) * 1000)
    route = d.get('route', 'unknown')
    classify_ms = d.get('classification_ms') or 0
    stream = any(s.get('response_content') == '[streamed]' for s in d.get('steps', []))

    # Sum provider call durations (excludes classification and enrichment)
    inference_ms = sum(
        s.get('duration_ms', 0) for s in d.get('steps', [])
        if s.get('step') == 'provider_call'
    )
    # Enrichment duration (xAI context fetch, only present on enrich route)
    enrich_ms = sum(
        s.get('duration_ms', 0) for s in d.get('steps', [])
        if s.get('step') == 'enrichment'
    )

    parts = [
        f"REQUEST session={d['id']} route={route} classification_ms={classify_ms}",
    ]
    if enrich_ms:
        parts.append(f"enrichment_ms={enrich_ms}")
    parts.append(f"inference_ms={inference_ms} total_ms={total_ms} stream={stream}")
    logger.info(" ".join(parts))

    threshold = SLOW_REQUEST_THRESHOLDS.get(route, 10000)
    if total_ms > threshold:
        slow_parts = [
            f"SLOW_REQUEST session={d['id']} route={route} total_ms={total_ms}",
            f"classification_ms={classify_ms}",
        ]
        if enrich_ms:
            slow_parts.append(f"enrichment_ms={enrich_ms}")
        slow_parts.append(f"inference_ms={inference_ms}")
        logger.warning(" ".join(slow_parts))


def _check_health(url, headers=None):
    """Check a single backend's health. Returns True if reachable and 200."""
    try:
        return requests.get(url, headers=headers, timeout=5).status_code == 200
    except Exception:
        return False


@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint.

    Checks all backends in parallel so a single slow/down backend
    doesn't block the others. Worst case drops from 15s to 5s.
    """
    health_start = time.time()
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            router_future = pool.submit(_check_health, f"{ROUTER_URL}/health")
            primary_future = pool.submit(_check_health, f"{PRIMARY_URL}/health")
            xai_future = None
            if XAI_API_KEY:
                xai_future = pool.submit(
                    _check_health,
                    f"{XAI_API_URL}/v1/models",
                    {'Authorization': f'Bearer {XAI_API_KEY}'},
                )

            router_health = router_future.result()
            primary_health = primary_future.result()
            xai_health = xai_future.result() if xai_future else None

        health_status = {
            'status': 'healthy' if (router_health and primary_health) else 'degraded',
            'router_model': 'healthy' if router_health else 'unhealthy',
            'primary_model': 'healthy' if primary_health else 'unhealthy'
        }

        if xai_health is not None:
            health_status['xai_model'] = 'healthy' if xai_health else 'unhealthy'

        health_ms = (time.time() - health_start) * 1000
        logger.info(f"Health check: status={health_status['status']} duration_ms={health_ms:.0f}")

        return jsonify(health_status), 200 if (router_health and primary_health) else 503

    except Exception as e:
        health_ms = (time.time() - health_start) * 1000
        logger.warning(f"Health check: status=unhealthy duration_ms={health_ms:.0f} error={e}")
        return jsonify({
            'status': 'unhealthy',
            'error': str(e)
        }), 503


def _handle_speculative_primary(spec_response, spec_start, data, is_stream, session):
    """Handle a successful speculative primary response.

    Logs the provider call step and returns a Flask Response.
    For streaming, returns an SSE iterator wrapping the speculative connection.
    For non-streaming, returns the already-complete response body.
    """
    logger.info("Using speculative primary response")
    spec_url = f"{PRIMARY_URL}/v1/chat/completions"
    log_params = {k: v for k, v in data.items()
                  if k not in ('messages', '_route', 'max_tokens')}

    if is_stream:
        # Streaming: the SSE connection is already open from the speculative
        # request.  Pipe chunks straight to the client — TTFT is immediate
        # since we don't wait for classification to finish before the
        # connection was established.
        session.begin_step('provider_call', 'primary', spec_url, PRIMARY_MODEL,
                           params=log_params)
        session.end_step(status=spec_response.status_code,
                         response_content='[streamed]')
        connect_ms = (time.time() - spec_start) * 1000

        def _stream_chunks():
            first_chunk = True
            for chunk in spec_response.iter_content(chunk_size=None):
                if first_chunk:
                    ttft_ms = (time.time() - spec_start) * 1000
                    logger.info(
                        f"Provider response: primary status={spec_response.status_code}"
                        f" connect_ms={connect_ms:.0f} ttft_ms={ttft_ms:.0f}"
                        f" stream=true speculative=true"
                    )
                    first_chunk = False
                yield chunk

        data['_route'] = 'primary'
        _log_request_summary(session)
        session.save()
        return Response(
            _stream_chunks(),
            status=spec_response.status_code,
            content_type='text/event-stream'
        )

    # Non-streaming: the full response body is already available because
    # inference ran in parallel with classification.
    response_body = spec_response.content
    forward_ms = (time.time() - spec_start) * 1000

    session.begin_step('provider_call', 'primary', spec_url, PRIMARY_MODEL,
                       params=log_params)
    # Backdate step start so end_step computes duration from speculative start
    session._step_start = spec_start

    finish_reason = None
    try:
        resp_json = json.loads(response_body)
        choice = resp_json.get('choices', [{}])[0]
        finish_reason = choice.get('finish_reason')
        msg = choice.get('message', {})
        resp_text = msg.get('content') or msg.get('reasoning_content') or ''
        session.end_step(
            status=spec_response.status_code,
            response_content=resp_text or response_body.decode('utf-8', errors='replace'),
            finish_reason=finish_reason
        )
    except (json.JSONDecodeError, IndexError, KeyError):
        session.end_step(
            status=spec_response.status_code,
            response_content=response_body.decode('utf-8', errors='replace')
        )

    logger.info(
        f"Provider response: primary status={spec_response.status_code}"
        f" duration_ms={forward_ms:.0f} finish_reason={finish_reason}"
        f" stream=false speculative=true"
    )

    data['_route'] = 'primary'
    _log_request_summary(session)
    session.save()
    return Response(
        response_body,
        status=spec_response.status_code,
        content_type=spec_response.headers.get('Content-Type', 'application/json')
    )


def _handle_primary(data, is_stream, session, date_ctx, spec_response, spec_start):
    """Handle primary route: use speculative response or fall back to normal forwarding.

    The speculative request was fired in parallel with classification.  If it
    succeeded (status 2xx), we reuse it directly — saving the full classification
    latency.  Otherwise we close it and forward normally.
    """
    if 'max_tokens' in data:
        logger.info(f"Primary route: removing client max_tokens ({data['max_tokens']})")

    if spec_response is not None and spec_response.ok:
        return _handle_speculative_primary(
            spec_response, spec_start, data, is_stream, session
        )

    # Speculative request failed — fall back to normal forwarding
    if spec_response is not None:
        logger.warning(f"Speculative primary status {spec_response.status_code}, falling back")
        spec_response.close()
    else:
        logger.warning("Speculative primary failed, falling back")

    if 'max_tokens' in data:
        del data['max_tokens']
    data['_route'] = 'primary'
    result = forward_request(PRIMARY_URL, '/v1/chat/completions', data, 'primary',
                             session=session, date_ctx=date_ctx)
    _log_request_summary(session)
    session.save()
    return result


def _handle_enrich(data, session, date_ctx):
    """Handle enrichment pipeline: fetch context from xAI, then forward to primary.

    Two-hop pipeline — xAI retrieves real-time context, which is injected into the
    conversation before the primary model generates the final response.
    """
    logger.info("Entering enrichment pipeline")
    context = fetch_enrichment_context(data['messages'], session=session, date_ctx=date_ctx)

    if context:
        injection = ENRICHMENT_INJECTION_PROMPT.format(
            context=context,
            date=date_ctx
        )
        # Append enrichment context to an existing system message,
        # or insert one before the last user message
        first_system = next((m for m in data['messages'] if m.get('role') == 'system'), None)
        if first_system:
            first_system['content'] = f"{first_system['content']}\n\n{injection}"
        else:
            insert_pos = len(data['messages']) - 1
            data['messages'].insert(insert_pos, {
                "role": "system",
                "content": injection
            })
        logger.info("Enrichment context injected, forwarding to primary model")
    else:
        logger.warning("Enrichment context unavailable, forwarding to primary model without context")

    # Enrich hits the primary (reasoning) model — strip max_tokens
    # so the model generates until its natural stop token.
    if 'max_tokens' in data:
        logger.info(f"Enrich route: removing client max_tokens ({data['max_tokens']})")
        del data['max_tokens']

    data['_route'] = 'enrich'
    result = forward_request(PRIMARY_URL, '/v1/chat/completions', data, 'primary',
                             session=session, date_ctx=date_ctx)
    _log_request_summary(session)
    session.save()
    return result


def _handle_meta(data, session, date_ctx):
    """Handle meta pipeline: client-generated meta-prompts (titles, follow-ups, summaries).

    These are self-contained prompts from clients like Open WebUI that embed their
    own conversation history.  They skip classification and go straight to primary.
    """
    logger.info("Entering meta pipeline")
    data['messages'].insert(0, {"role": "system", "content": META_SYSTEM_PROMPT})

    # Meta hits the primary (reasoning) model — strip max_tokens.
    if 'max_tokens' in data:
        logger.info(f"Meta route: removing client max_tokens ({data['max_tokens']})")
        del data['max_tokens']

    data['_route'] = 'meta'
    result = forward_request(PRIMARY_URL, '/v1/chat/completions', data, 'primary',
                             session=session, date_ctx=date_ctx)
    _log_request_summary(session)
    session.save()
    return result


def _handle_xai(data, route, session, date_ctx):
    """Handle xAI route: forward to cloud API with max_tokens floor.

    Enforces XAI_MIN_MAX_TOKENS so Open WebUI's low defaults (100–300) don't
    truncate substantive answers from the cloud model.
    """
    target_url = get_model_url(route)

    client_max = data.get('max_tokens') or 0
    if client_max < XAI_MIN_MAX_TOKENS:
        logger.info(f"xAI route: raising max_tokens from {client_max} to {XAI_MIN_MAX_TOKENS}")
        data['max_tokens'] = XAI_MIN_MAX_TOKENS

    data['_route'] = route
    result = forward_request(target_url, '/v1/chat/completions', data, route,
                             session=session, date_ctx=date_ctx)
    _log_request_summary(session)
    session.save()
    return result


@app.route('/v1/chat/completions', methods=['POST'])
def chat_completions():
    """
    Main chat completions endpoint with intelligent routing.
    Compatible with OpenAI API format.

    Speculative execution: fires a primary model request in parallel with
    classification.  ~80% of requests route to primary (SIMPLE/MODERATE),
    so the speculative request usually saves ~1–1.8s of classification
    latency.  For streaming, this drops TTFT from ~1s to ~48ms.

    Cost: one wasted local inference start per COMPLEX/ENRICH request
    (~200–400ms GPU time), negligible at single-user homelab concurrency.
    """
    session = SessionLogger()
    spec_response = None  # track for cleanup on error
    try:
        data = request.get_json()

        if not data or 'messages' not in data:
            return jsonify({
                'error': 'Invalid request',
                'message': 'Missing required field: messages'
            }), 400

        session.set_query(data['messages'])

        # Log request context size for latency correlation
        msg_count = len(data['messages'])
        total_chars = sum(len(m.get('content', '')) for m in data['messages'])
        is_stream = data.get('stream', False)
        logger.info(f"Incoming request: messages={msg_count} total_chars={total_chars} stream={is_stream}")

        # Compute temporal context once for the entire request pipeline
        date_ctx = date_context()

        # Fire classification and speculative primary inference in parallel.
        # determine_route() calls the Orchestrator 8B classifier (~1–1.8s).
        # start_speculative_primary() sends the same request to the primary
        # model immediately, betting that classification will return primary.
        with ThreadPoolExecutor(max_workers=2) as pool:
            classify_future = pool.submit(
                determine_route, data['messages'], session=session, date_ctx=date_ctx
            )
            spec_future = pool.submit(
                start_speculative_primary, data, date_ctx, is_stream
            )
            route = classify_future.result()
            spec_response, spec_start = spec_future.result()

        # Non-primary routes: cancel the speculative request immediately
        if route != 'primary' and spec_response is not None:
            spec_response.close()
            logger.info(f"Cancelled speculative primary (route={route})")
            spec_response = None

        # Dispatch to route handler
        if route == 'primary':
            return _handle_primary(data, is_stream, session, date_ctx, spec_response, spec_start)
        if route == 'enrich':
            return _handle_enrich(data, session, date_ctx)
        if route == 'meta':
            return _handle_meta(data, session, date_ctx)
        return _handle_xai(data, route, session, date_ctx)

    except Exception as e:
        if spec_response is not None:
            spec_response.close()
        logger.error(f"Error in chat_completions: {str(e)}")
        session.set_error(str(e))
        _log_request_summary(session)
        session.save()
        return jsonify({
            'error': 'Internal error',
            'message': str(e)
        }), 500


@app.route('/v1/completions', methods=['POST'])
def completions():
    """
    Legacy completions endpoint (non-chat format).
    Routes to primary model by default.
    """
    try:
        data = request.get_json()

        if not data or 'prompt' not in data:
            return jsonify({
                'error': 'Invalid request',
                'message': 'Missing required field: prompt'
            }), 400

        # Use primary model for legacy completions
        return forward_request(PRIMARY_URL, '/v1/completions', data)

    except Exception as e:
        logger.error(f"Error in completions: {str(e)}")
        return jsonify({
            'error': 'Internal error',
            'message': str(e)
        }), 500


@app.route('/v1/models', methods=['GET'])
def list_models():
    """
    Present a single virtual model to external consumers.
    Callers (e.g. Open WebUI) see one model — routing is invisible.
    The virtual model name is configurable via VIRTUAL_MODEL env var.
    """
    return jsonify({
        'object': 'list',
        'data': [{
            'id': VIRTUAL_MODEL,
            'object': 'model',
            'owned_by': 'ai-router',
        }]
    })


@app.route('/api/route', methods=['POST'])
def api_route():
    """
    Explicit routing endpoint for testing.
    Allows client to specify which model to use.
    """
    try:
        data = request.get_json()

        if not data:
            return jsonify({'error': 'Invalid request'}), 400

        route = data.get('route', 'auto')

        if route == 'auto':
            # Automatic routing
            if 'messages' in data:
                route = determine_route(data['messages'])
            else:
                route = 'primary'
        elif route not in ['primary', 'xai', 'enrich']:
            return jsonify({
                'error': 'Invalid route',
                'message': 'Route must be "primary", "xai", "enrich", or "auto"'
            }), 400

        target_url = get_model_url(route)
        path = data.get('path', '/v1/chat/completions')

        return forward_request(target_url, path, data.get('data', {}))

    except Exception as e:
        logger.error(f"Error in api_route: {str(e)}")
        return jsonify({
            'error': 'Internal error',
            'message': str(e)
        }), 500


@app.route('/stats', methods=['GET'])
def stats():
    """Get routing statistics (placeholder for future implementation)."""
    # This would track routing decisions in a production system
    return jsonify({
        'message': 'Statistics endpoint - not yet implemented',
        'routes': {
            'primary': 'Local model for simple and moderate queries',
            'xai': 'Cloud model for complex queries and enrichment'
        }
    })


@app.route('/', methods=['GET'])
def root():
    """Root endpoint with API information."""
    return jsonify({
        'service': 'AI Router',
        'version': '1.0.0',
        'model': VIRTUAL_MODEL,
        'endpoints': {
            '/health': 'Health check',
            '/v1/chat/completions': 'Chat completions with auto-routing',
            '/v1/completions': 'Legacy completions',
            '/v1/models': 'List available models',
            '/api/route': 'Explicit routing control',
            '/stats': 'Routing statistics'
        }
    })


def main():
    """Start the Flask application."""
    logger.info("Starting AI Router service...")
    logger.info(f"Router model: {ROUTER_URL}")
    logger.info(f"Primary model: {PRIMARY_URL}")

    app.run(
        host='0.0.0.0',
        port=8002,
        debug=False,
        threaded=True
    )
