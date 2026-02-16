"""Flask application and route handlers."""

from flask import Flask, request, jsonify
import requests

from src.config import (
    logger, date_context,
    ROUTER_URL, PRIMARY_URL,
    XAI_API_KEY, XAI_API_URL, XAI_MODEL,
    ROUTER_MODEL, PRIMARY_MODEL,
    VIRTUAL_MODEL,
    ENRICHMENT_INJECTION_PROMPT,
    META_SYSTEM_PROMPT,
)
from src.session_logger import SessionLogger
from src.providers import (
    determine_route,
    fetch_enrichment_context,
    get_model_url,
    forward_request,
)

app = Flask(__name__)


@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint."""
    try:
        # Check if local models are reachable
        router_health = requests.get(f"{ROUTER_URL}/health", timeout=5).status_code == 200
        primary_health = requests.get(f"{PRIMARY_URL}/health", timeout=5).status_code == 200

        # xAI health is optional (only check if API key is configured)
        xai_health = None
        if XAI_API_KEY:
            try:
                xai_response = requests.get(
                    f"{XAI_API_URL}/v1/models",
                    headers={'Authorization': f'Bearer {XAI_API_KEY}'},
                    timeout=5
                )
                xai_health = xai_response.status_code == 200
            except:
                xai_health = False

        health_status = {
            'status': 'healthy' if (router_health and primary_health) else 'degraded',
            'router_model': 'healthy' if router_health else 'unhealthy',
            'primary_model': 'healthy' if primary_health else 'unhealthy'
        }

        if xai_health is not None:
            health_status['xai_model'] = 'healthy' if xai_health else 'unhealthy'

        return jsonify(health_status), 200 if (router_health and primary_health) else 503

    except Exception as e:
        return jsonify({
            'status': 'unhealthy',
            'error': str(e)
        }), 503


@app.route('/v1/chat/completions', methods=['POST'])
def chat_completions():
    """
    Main chat completions endpoint with intelligent routing.
    Compatible with OpenAI API format.
    """
    session = SessionLogger()
    try:
        data = request.get_json()

        if not data or 'messages' not in data:
            return jsonify({
                'error': 'Invalid request',
                'message': 'Missing required field: messages'
            }), 400

        session.set_query(data['messages'])

        # Determine routing using Orchestrator 8B classification
        route = determine_route(data['messages'], session=session)

        # Enrichment pipeline: fetch context from xAI, then forward to primary
        if route == 'enrich':
            logger.info("Entering enrichment pipeline")
            context = fetch_enrichment_context(data['messages'], session=session)

            if context:
                injection = ENRICHMENT_INJECTION_PROMPT.format(
                    context=context,
                    date=date_context()
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

            data['_route'] = 'enrich'
            result = forward_request(PRIMARY_URL, '/v1/chat/completions', data, 'primary', session=session)
            session.save()
            return result

        # Meta pipeline: client-generated meta-prompts (follow-up suggestions,
        # title generation, summaries). Self-contained, no enrichment needed.
        if route == 'meta':
            logger.info("Entering meta pipeline")
            data['messages'].insert(0, {"role": "system", "content": META_SYSTEM_PROMPT})
            data['_route'] = 'meta'
            result = forward_request(PRIMARY_URL, '/v1/chat/completions', data, 'primary', session=session)
            session.save()
            return result

        target_url = get_model_url(route)

        # Add routing metadata to response
        # (Note: This modifies the request, which the model will ignore)
        data['_route'] = route

        # Forward to appropriate model
        result = forward_request(target_url, '/v1/chat/completions', data, route, session=session)
        session.save()
        return result

    except Exception as e:
        logger.error(f"Error in chat_completions: {str(e)}")
        session.set_error(str(e))
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
