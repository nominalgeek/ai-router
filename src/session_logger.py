"""Session-based request logging — one JSON file per request lifecycle."""

import copy
import os
import json
import uuid
import time
import glob as globmod

from src.config import logger, now, LOG_DIR

# Session logging configuration — session JSONs go in a subdirectory of LOG_DIR
SESSIONS_DIR = os.path.join(LOG_DIR, 'sessions')
LOG_MAX_AGE_DAYS = int(os.getenv('LOG_MAX_AGE_DAYS', '7'))
LOG_MAX_COUNT = int(os.getenv('LOG_MAX_COUNT', '5000'))
os.makedirs(SESSIONS_DIR, exist_ok=True)


class SessionLogger:
    """Captures the full lifecycle of a single request as a JSON session file."""

    def __init__(self):
        self.id = uuid.uuid4().hex[:8]
        self.start_time = time.time()
        self.timestamp = now()
        self.data = {
            'id': self.id,
            'timestamp': self.timestamp.isoformat(timespec='milliseconds'),
            'user_query': None,
            'client_messages': None,
            'route': None,
            'classification_raw': None,
            'classification_ms': None,
            'steps': [],
            'total_ms': None,
            'error': None,
        }
        self._step_start = None

    def set_query(self, messages):
        """Store the full original messages and extract the last user message as the query."""
        if messages:
            self.data['client_messages'] = copy.deepcopy(messages)
            for msg in reversed(messages):
                if msg.get('role') == 'user':
                    content = msg.get('content', '')
                    self.data['user_query'] = content[:500] if len(content) > 500 else content
                    break

    def set_route(self, route, raw_decision, duration_ms):
        self.data['route'] = route
        self.data['classification_raw'] = raw_decision
        self.data['classification_ms'] = round(duration_ms)

    def begin_step(self, step, provider, url, model, messages=None, params=None):
        """Start timing a step and record its request data."""
        self._step_start = time.time()
        step_entry = {
            'step': step,
            'provider': provider,
            'url': url,
            'model': model,
        }
        if messages is not None:
            step_entry['messages_sent'] = messages
        if params is not None:
            step_entry['params'] = params
        step_entry['duration_ms'] = None
        step_entry['status'] = None
        step_entry['response_content'] = None
        self.data['steps'].append(step_entry)

    def end_step(self, status=None, response_content=None, finish_reason=None, error=None):
        """Finish timing the current step and record its result."""
        if not self.data['steps']:
            return
        step = self.data['steps'][-1]
        if self._step_start:
            step['duration_ms'] = round((time.time() - self._step_start) * 1000)
        step['status'] = status
        step['finish_reason'] = finish_reason
        if error:
            step['response_content'] = f"[error: {error}]"
        elif response_content is not None:
            step['response_content'] = response_content[:2000] if len(str(response_content)) > 2000 else response_content
        self._step_start = None

    def set_error(self, error):
        self.data['error'] = str(error)

    def save(self):
        """Write session to a JSON file and run cleanup."""
        self.data['total_ms'] = round((time.time() - self.start_time) * 1000)
        ts = self.timestamp.strftime('%Y-%m-%d_%H-%M-%S')
        filename = f"{ts}_{self.id}.json"
        filepath = os.path.join(SESSIONS_DIR, filename)
        try:
            with open(filepath, 'w') as f:
                json.dump(self.data, f, indent=2, default=str)
        except Exception as e:
            logger.error(f"Failed to write session log: {e}")
        self._cleanup()

    def _cleanup(self):
        """Remove old session files if over age or count limits."""
        try:
            files = sorted(globmod.glob(os.path.join(SESSIONS_DIR, '*.json')))
            # Remove files exceeding count limit (oldest first)
            if len(files) > LOG_MAX_COUNT:
                for f in files[:len(files) - LOG_MAX_COUNT]:
                    os.remove(f)
            # Remove files older than max age
            cutoff = time.time() - (LOG_MAX_AGE_DAYS * 86400)
            for f in files:
                if os.path.getmtime(f) < cutoff:
                    os.remove(f)
        except Exception as e:
            logger.warning(f"Session log cleanup error: {e}")
