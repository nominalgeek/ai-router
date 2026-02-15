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

# Routing strategy configuration
SIMPLE_QUERY_MAX_LENGTH = 100
SIMPLE_QUERY_KEYWORDS = [
    'hello', 'hi', 'what is', 'who is', 'when is', 'where is',
    'define', 'meaning', 'quick question', 'yes', 'no', 'thanks'
]


def determine_route(messages: list) -> str:
    """
    Determine which model to use based on request characteristics.
    
    Args:
        messages: List of message dictionaries
        
    Returns:
        'router' or 'primary'
    """
    if not messages:
        return 'primary'
    
    # Get the last user message
    last_message = messages[-1].get('content', '')
    
    # Simple heuristics for routing
    message_length = len(last_message)
    message_lower = last_message.lower()

    # Priority 1: Check for complex reasoning keywords first
    reasoning_keywords = [
        'explain', 'analyze', 'compare', 'why', 'how does',
        'reason', 'think', 'elaborate', 'detail', 'complex',
        'prove', 'calculate', 'solve', 'derive'
    ]

    if any(keyword in message_lower for keyword in reasoning_keywords):
        logger.info("Routing to primary model: reasoning required")
        return 'primary'

    # Priority 2: Long conversations (context matters)
    if len(messages) > 3:
        logger.info("Routing to primary model: long conversation context")
        return 'primary'

    # Priority 3: Route to fast model for simple queries
    # 1. Short queries
    if message_length < SIMPLE_QUERY_MAX_LENGTH:
        logger.info(f"Routing to router model: short query ({message_length} chars)")
        return 'router'

    # 2. Simple keyword matches
    for keyword in SIMPLE_QUERY_KEYWORDS:
        if message_lower.startswith(keyword):
            logger.info(f"Routing to router model: simple keyword '{keyword}'")
            return 'router'

    # 3. Single-word or very basic questions
    if len(last_message.split()) <= 5:
        logger.info("Routing to router model: very short question")
        return 'router'

    # Default to primary for better quality
    logger.info("Routing to primary model: default route")
    return 'primary'


def get_model_url(route: str) -> str:
    """Get the appropriate model URL based on route."""
    return ROUTER_URL if route == 'router' else PRIMARY_URL


def forward_request(target_url: str, path: str, data: Dict[Any, Any]) -> Response:
    """
    Forward request to target model with proper error handling.
    
    Args:
        target_url: Base URL of target model
        path: API path (e.g., '/v1/chat/completions')
        data: Request payload
        
    Returns:
        Flask Response object
    """
    try:
        url = f"{target_url}{path}"
        logger.info(f"Forwarding request to {url}")
        
        # Forward the request
        response = requests.post(
            url,
            json=data,
            headers={'Content-Type': 'application/json'},
            timeout=300  # 5 minute timeout for long generations
        )
        
        # Return response with same status code
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
        # Check if both models are reachable
        router_health = requests.get(f"{ROUTER_URL}/health", timeout=5).status_code == 200
        primary_health = requests.get(f"{PRIMARY_URL}/health", timeout=5).status_code == 200
        
        return jsonify({
            'status': 'healthy' if (router_health and primary_health) else 'degraded',
            'router_model': 'healthy' if router_health else 'unhealthy',
            'primary_model': 'healthy' if primary_health else 'unhealthy'
        }), 200 if (router_health and primary_health) else 503
        
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
        
        # Determine routing
        route = determine_route(data['messages'])
        target_url = get_model_url(route)
        
        # Add routing metadata to response
        # (Note: This modifies the request, which the model will ignore)
        data['_route'] = route
        
        # Forward to appropriate model
        return forward_request(target_url, '/v1/chat/completions', data)
        
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