#!/usr/bin/env python3
"""
AI Router - Intelligent request routing between vLLM models

Routes requests to either:
- Router model (fast, for simple queries)
- Primary model (powerful, for complex reasoning)
"""

import os
import json
import logging
from flask import Flask, request, jsonify, Response
import requests
from typing import Dict, Any, Optional

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Configuration
ROUTER_URL = os.getenv('ROUTER_URL', 'http://router:8001')
PRIMARY_URL = os.getenv('PRIMARY_URL', 'http://primary:8000')
XAI_API_KEY = os.getenv('XAI_API_KEY', '')
XAI_API_URL = 'https://api.x.ai'  # Base URL without /v1
# Available models: grok-4-1-fast-non-reasoning, grok-4-1-fast-reasoning, grok-code-fast-1
XAI_MODEL = os.getenv('XAI_MODEL', 'grok-4-1-fast-reasoning')
ROUTER_MODEL = os.getenv('ROUTER_MODEL', 'nvidia/Nemotron-Mini-4B-Instruct')
PRIMARY_MODEL = os.getenv('PRIMARY_MODEL', 'unsloth/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4')

# Load routing prompts from external files
ROUTING_PROMPT_PATH = os.getenv('ROUTING_PROMPT_PATH', '/app/config/routing-prompt.md')
ROUTING_SYSTEM_PROMPT_PATH = os.getenv('ROUTING_SYSTEM_PROMPT_PATH', '/app/config/routing-system-prompt.md')

def load_prompt_file(path, fallback, label):
    """Load a prompt template from an external markdown file."""
    try:
        with open(path, 'r') as f:
            prompt = f.read().strip()
            logger.info(f"Loaded {label} from {path}")
            return prompt
    except FileNotFoundError:
        logger.error(f"{label} not found at {path}, using fallback")
        return fallback

ROUTING_SYSTEM_PROMPT = load_prompt_file(
    ROUTING_SYSTEM_PROMPT_PATH,
    'You are a query classifier. Respond with ONLY ONE WORD: SIMPLE, MODERATE, or COMPLEX.',
    'routing system prompt'
)

ROUTING_PROMPT = load_prompt_file(
    ROUTING_PROMPT_PATH,
    ('Classify this query as SIMPLE, MODERATE, or COMPLEX.\n'
     'User query: "{query}"\n'
     'Respond with ONLY ONE WORD: SIMPLE, MODERATE, or COMPLEX'),
    'routing prompt'
)


def determine_route(messages: list) -> str:
    """
    Use Mini 4B to determine routing via prompt-based classification.
    Routes to: 'router' (Mini 4B), 'primary' (local Nano 30B), or 'xai' (xAI API).

    Args:
        messages: List of message dictionaries

    Returns:
        'router', 'primary', or 'xai'
    """
    if not messages:
        return 'primary'

    # Get the last user message
    last_message = messages[-1].get('content', '')

    # Build routing classification prompt from external template
    routing_prompt = ROUTING_PROMPT.format(query=last_message)

    try:
        # Ask Mini 4B router to classify the query
        response = requests.post(
            f"{ROUTER_URL}/v1/chat/completions",
            json={
                "messages": [
                    {"role": "system", "content": ROUTING_SYSTEM_PROMPT},
                    {"role": "user", "content": routing_prompt}
                ],
                "max_tokens": 10,
                "temperature": 0.0  # Deterministic
            },
            timeout=10  # Timeout for routing decision
        )

        if response.status_code == 200:
            result = response.json()
            # Extract decision from response (handle both content and reasoning_content)
            message = result['choices'][0]['message']
            decision = (message.get('content') or message.get('reasoning_content') or '').strip().upper()

            if 'SIMPLE' in decision:
                logger.info("Routing to router model: prompt-based classification (SIMPLE)")
                return 'router'
            elif 'MODERATE' in decision:
                logger.info("Routing to primary model: prompt-based classification (MODERATE)")
                return 'primary'
            elif 'COMPLEX' in decision:
                logger.info("Routing to xAI model: prompt-based classification (COMPLEX)")
                return 'xai'

        # Fallback to primary if classification fails
        logger.warning(f"Routing classification unclear: '{decision}', defaulting to primary")
        return 'primary'

    except requests.exceptions.Timeout:
        logger.warning("Routing classification timeout, defaulting to primary")
        return 'primary'
    except Exception as e:
        logger.error(f"Error in prompt-based routing: {str(e)}, defaulting to primary")
        return 'primary'


def get_model_url(route: str) -> str:
    """Get the appropriate model URL based on route."""
    if route == 'router':
        return ROUTER_URL
    elif route == 'xai':
        return XAI_API_URL
    else:
        return PRIMARY_URL


def forward_request(target_url: str, path: str, data: Dict[Any, Any], route: str = None) -> Response:
    """
    Forward request to target model with proper error handling.

    Args:
        target_url: Base URL of target model
        path: API path (e.g., '/v1/chat/completions')
        data: Request payload
        route: Route type ('router', 'primary', 'xai')

    Returns:
        Flask Response object
    """
    try:
        url = f"{target_url}{path}"
        logger.info(f"Forwarding request to {url}")

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

        # Forward the request
        response = requests.post(
            url,
            json=data,
            headers=headers,
            stream=is_stream,
            timeout=300  # 5 minute timeout for long generations
        )

        if is_stream:
            # Stream SSE chunks back to the client
            return Response(
                response.iter_content(chunk_size=None),
                status=response.status_code,
                content_type='text/event-stream'
            )

        # Return buffered response with same status code
        return Response(
            response.content,
            status=response.status_code,
            content_type=response.headers.get('Content-Type', 'application/json')
        )

    except requests.exceptions.Timeout:
        logger.error(f"Request timeout to {target_url}")
        return jsonify({
            'error': 'Request timeout',
            'message': 'The model took too long to respond'
        }), 504

    except requests.exceptions.ConnectionError:
        logger.error(f"Connection error to {target_url}")
        return jsonify({
            'error': 'Service unavailable',
            'message': f'Cannot connect to model service'
        }), 503

    except Exception as e:
        logger.error(f"Error forwarding request: {str(e)}")
        return jsonify({
            'error': 'Internal error',
            'message': str(e)
        }), 500


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
                    f"{XAI_API_URL}/models",
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
    try:
        data = request.get_json()
        
        if not data or 'messages' not in data:
            return jsonify({
                'error': 'Invalid request',
                'message': 'Missing required field: messages'
            }), 400
        
        # Determine routing using Mini 4B classification
        route = determine_route(data['messages'])
        target_url = get_model_url(route)

        # Add routing metadata to response
        # (Note: This modifies the request, which the model will ignore)
        data['_route'] = route

        # Forward to appropriate model
        return forward_request(target_url, '/v1/chat/completions', data, route)
        
    except Exception as e:
        logger.error(f"Error in chat_completions: {str(e)}")
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
    """List available models from both services."""
    try:
        router_models = requests.get(f"{ROUTER_URL}/v1/models", timeout=5).json()
        primary_models = requests.get(f"{PRIMARY_URL}/v1/models", timeout=5).json()
        
        # Combine model lists
        all_models = {
            'object': 'list',
            'data': router_models.get('data', []) + primary_models.get('data', [])
        }
        
        return jsonify(all_models)
        
    except Exception as e:
        logger.error(f"Error listing models: {str(e)}")
        return jsonify({
            'error': 'Internal error',
            'message': str(e)
        }), 500


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
        elif route not in ['router', 'primary']:
            return jsonify({
                'error': 'Invalid route',
                'message': 'Route must be "router", "primary", or "auto"'
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
            'router': 'Fast model for simple queries',
            'primary': 'Powerful model for complex reasoning'
        }
    })


@app.route('/', methods=['GET'])
def root():
    """Root endpoint with API information."""
    return jsonify({
        'service': 'AI Router',
        'version': '1.0.0',
        'endpoints': {
            '/health': 'Health check',
            '/v1/chat/completions': 'Chat completions with auto-routing',
            '/v1/completions': 'Legacy completions',
            '/v1/models': 'List available models',
            '/api/route': 'Explicit routing control',
            '/stats': 'Routing statistics'
        },
        'models': {
            'router': ROUTER_URL,
            'primary': PRIMARY_URL
        }
    })


if __name__ == '__main__':
    logger.info("Starting AI Router service...")
    logger.info(f"Router model: {ROUTER_URL}")
    logger.info(f"Primary model: {PRIMARY_URL}")
    
    # Run Flask app
    app.run(
        host='0.0.0.0',
        port=8002,
        debug=False,  # Set to True for development
        threaded=True
    )