"""Session-based request logging — one JSON file per request lifecycle."""

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

    # Cleanup runs periodically instead of every save() call.
    # At low file counts cleanup is <1ms, but glob + sort over 5,000 files
    # will become measurable — this keeps it off the hot path.
    _save_count = 0
    _last_cleanup = time.time()
    CLEANUP_INTERVAL = 100   # run cleanup every N saves
    CLEANUP_PERIOD = 60      # ... or every N seconds, whichever comes first

    def __init__(self):
        self.id = uuid.uuid4().hex[:8]
        self.start_time = time.time()
        self.timestamp = now()
        self.data = {
            'id': self.id,
            'timestamp': self.timestamp.isoformat(timespec='milliseconds'),
            'client_ip': None,
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
        self._messages_json = None  # pre-serialized client messages (set by set_query)

    def set_query(self, messages):
        """Snapshot the original messages as a JSON string (avoids deep copy).

        We need JSON for the log file anyway, so serializing once here is
        cheaper than copy.deepcopy() — especially for long multi-turn
        conversations with reasoning blocks.
        """
        if messages:
            self._messages_json = json.dumps(messages, default=str)
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

        write_start = time.time()
        try:
            # Embed the pre-serialized client_messages JSON string directly
            # so we don't re-serialize the (potentially large) conversation.
            if self._messages_json:
                self.data['client_messages'] = '__MESSAGES_PLACEHOLDER__'
            with open(filepath, 'w') as f:
                raw = json.dumps(self.data, indent=2, default=str)
                if self._messages_json:
                    raw = raw.replace('"__MESSAGES_PLACEHOLDER__"', self._messages_json)
                f.write(raw)
        except Exception as e:
            logger.error(f"Failed to write session log: {e}")
        write_ms = (time.time() - write_start) * 1000

        # Run cleanup periodically, not every request
        cleanup_ms = 0
        SessionLogger._save_count += 1
        if (SessionLogger._save_count >= SessionLogger.CLEANUP_INTERVAL
                or time.time() - SessionLogger._last_cleanup > SessionLogger.CLEANUP_PERIOD):
            cleanup_start = time.time()
            self._cleanup()
            cleanup_ms = (time.time() - cleanup_start) * 1000
            SessionLogger._save_count = 0
            SessionLogger._last_cleanup = time.time()

        logger.info(f"Session saved: {self.id} write_ms={write_ms:.0f} cleanup_ms={cleanup_ms:.0f}")

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
