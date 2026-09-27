"""Explicit, bounded Gemini image/JSON calls with durable no-retry receipts.

The caller supplies credentials, model and budget. Cached successful responses
are content-addressed by source bytes, prompt, schema and generation policy.
A timed-out/reserved request is never resubmitted implicitly. API access is not
performed by construction or by importing this module.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import time

import requests

from .atomic_files import replace_with_retry
from .photo_semantics import _validate_manifest


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _sanitize(value, secret):
    if isinstance(value, str):
        return value.replace(secret, '[REDACTED]') if secret else value
    if isinstance(value, dict):
        return {_sanitize(k, secret): _sanitize(v, secret) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize(v, secret) for v in value]
    return value


def provider_schema(schema):
    """Keep the output shape, but move bounds out of Gemini's decoder grammar.

    Nested bounded arrays can multiply constrained-decoding states and produce
    an opaque INVALID_ARGUMENT. Google documents schema-complexity limits:
    https://ai.google.dev/gemini-api/docs/structured-output#limitations
    Bounds remain instructions and are still enforced by the domain validator.
    This is one deterministic request policy, never a retry after rejection.
    """
    bounds = {'minimum', 'maximum', 'minItems', 'maxItems', 'minLength', 'maxLength'}

    def adapt(node):
        if not isinstance(node, dict):
            raise ValueError('Response schema nodes must be objects')
        result = {}
        for key, value in node.items():
            if key in bounds:
                continue
            if key in ('properties', '$defs', 'definitions'):
                if not isinstance(value, dict):
                    raise ValueError('Response schema property definitions must be objects')
                result[key] = {name: adapt(child) for name, child in value.items()}
            elif key in ('items', 'additionalProperties') and isinstance(value, dict):
                result[key] = adapt(value)
            elif key in ('anyOf', 'oneOf', 'allOf', 'prefixItems'):
                result[key] = [adapt(child) for child in value]
            else:
                result[key] = value
        constraints = {key: node[key] for key in sorted(bounds) if key in node}
        if constraints:
            instruction = 'Required value constraints: ' + _json(constraints).decode() + '.'
            result['description'] = (str(node.get('description', '')) + ' ' + instruction).strip()
        return result

    # Round-trip before adapting: never mutate the caller's shared schema, and
    # reject nonfinite/non-JSON values before reserving a charged call.
    return adapt(json.loads(_json(schema)))


class GeminiSemanticClient:
    """Generic `.infer(manifest, prompt, schema)` for interpretation or review.

    `maximum_calls` is the lifetime budget of this cache directory, including
    uncertain/failed calls and calls by another client instance. A fresh budget
    requires an explicitly different directory. Never include facts, labels or
    filenames in prompts when running blind comparisons.
    """

    def __init__(self, *, api_key, model, cache_dir, maximum_calls=1,
                 maximum_output_tokens=4096, timeout_seconds=120, session=None):
        if not isinstance(api_key, str) or not api_key:
            raise ValueError('An explicit nonempty API credential is required')
        if not isinstance(model, str) or not re.fullmatch(r'gemini-[a-zA-Z0-9._-]+', model):
            raise ValueError('Invalid Gemini model identifier')
        if type(maximum_calls) is not int or not 1 <= maximum_calls <= 32:
            raise ValueError('Use an explicit one-to-thirty-two-call cache budget')
        if type(maximum_output_tokens) is not int or not 128 <= maximum_output_tokens <= 8192:
            raise ValueError('Output token budget must be 128..8192')
        if isinstance(timeout_seconds, bool) or not 1 <= timeout_seconds <= 180:
            raise ValueError('Timeout must be 1..180 seconds')
        self._credential = api_key
        self.model = model
        self.cache_dir = Path(cache_dir).resolve()
        self.maximum_calls = maximum_calls
        self.maximum_output_tokens = maximum_output_tokens
        self.timeout_seconds = timeout_seconds
        self._session = session or requests.Session()
        if session is None:
            # Disable redirects and retries. The platform's TLS verification is
            # retained; no credentials are placed in URLs or exception output.
            self._session.mount('https://', requests.adapters.HTTPAdapter(max_retries=0))

    def describe(self):
        return {'provider': 'gemini', 'model': self.model, 'cache_dir': str(self.cache_dir),
                'maximum_calls': self.maximum_calls, 'maximum_output_tokens': self.maximum_output_tokens,
                'network_policy': 'explicit_calls_no_retries_no_redirects', 'credential': 'explicit_redacted'}

    def _write(self, path, value):
        clean = _sanitize(value, self._credential)
        temporary = path.with_suffix(path.suffix + '.tmp')
        temporary.write_bytes(_json(clean))
        replace_with_retry(temporary, path)

    def _cached(self, key, image_manifest):
        receipt_path = self.cache_dir / (key + '.json')
        if receipt_path.exists():
            receipt = json.loads(receipt_path.read_bytes())
            if (receipt.get('request_sha256') != key
                    or _hash(_json(receipt.get('recipe'))) != key
                    or receipt.get('response_sha256') != _hash(_json(receipt.get('response')))):
                raise ValueError('Semantic cache response or recipe hash mismatch')
            if receipt.get('status') != 'complete':
                raise RuntimeError('Prior semantic request failed or completion is uncertain; no automatic retry')
            return {**receipt, 'source_images': image_manifest, 'cache_reused': True}
        if (self.cache_dir / (key + '.reserved')).exists():
            raise RuntimeError('Semantic request is already reserved; completion is uncertain and will not be retried')
        return None

    def infer(self, image_manifest, prompt, schema):
        _validate_manifest(image_manifest)
        if not isinstance(prompt, str) or not 1 <= len(prompt) <= 40000 or not isinstance(schema, dict):
            raise ValueError('Explicit bounded prompt and JSON response schema are required')
        config = {'candidateCount': 1, 'maxOutputTokens': self.maximum_output_tokens,
                  'thinkingConfig': {'thinkingLevel': 'low'}, 'responseMimeType': 'application/json',
                  'responseSchema': schema}
        recipe = {'model': self.model, 'source_sha256': [v['sha256'] for v in image_manifest],
                  'explicit_prompt_labels': [v.get('prompt_label') for v in image_manifest],
                  'prompt': prompt, 'generation_config': config, 'protocol': 'bounded_semantic_transport_v1'}
        # Check the original policy before constructing a changed request. A
        # software upgrade is not authorization to repeat a prior charged call.
        legacy_key = _hash(_json(recipe))
        config = {**config, 'responseSchema': provider_schema(schema)}
        recipe = {**recipe, 'generation_config': config, 'original_response_schema': schema,
                  'protocol': 'bounded_semantic_transport_v2',
                  'schema_policy': 'shape_and_enums_bounds_as_descriptions_v1'}
        key = _hash(_json(recipe))
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        budget_path = self.cache_dir / 'budget.json'
        try:
            descriptor = os.open(budget_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(_json({'maximum_calls': self.maximum_calls}))
        except FileExistsError:
            if json.loads(budget_path.read_bytes()) != {'maximum_calls': self.maximum_calls}:
                raise ValueError('Semantic cache lifetime call budget changed; use a new explicit cache directory')
        for cached_key in (legacy_key, key):
            cached = self._cached(cached_key, image_manifest)
            if cached is not None:
                return cached
        receipt_path = self.cache_dir / (key + '.json')
        reservation_path = self.cache_dir / (key + '.reserved')
        try:
            descriptor = os.open(reservation_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        except FileExistsError:
            raise RuntimeError('Semantic request is already reserved; completion is uncertain and will not be retried') from None
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(_json({'request_sha256': key, 'model': self.model}))
        reserved = None
        for number in range(self.maximum_calls):
            try:
                descriptor = os.open(self.cache_dir / f'call-{number:03d}.reserved', os.O_WRONLY | os.O_CREAT | os.O_EXCL)
                with os.fdopen(descriptor, 'wb') as stream:
                    stream.write(_json({'request_sha256': key}))
                reserved = number
                break
            except FileExistsError:
                continue
        if reserved is None:
            # This key's reservation remains durable: callers cannot accidentally
            # grow the budget and replay a formerly refused request.
            raise RuntimeError('Semantic API call budget exhausted; no request sent')
        record = {'schema_version': 1, 'request_sha256': key, 'status': 'reserved',
                  'model': self.model, 'source_images': image_manifest, 'recipe': recipe,
                  'response': None, 'response_sha256': _hash(_json(None)), 'cache_reused': False}
        self._write(receipt_path, record)
        started = time.monotonic()
        try:
            parts = [{'text': prompt}]
            for number, image in enumerate(image_manifest, 1):
                data = Path(image['local_path']).read_bytes()
                if _hash(data) != image['sha256']:
                    raise ValueError('Semantic image bytes changed after manifest capture')
                label = f'image-{number}'
                if image.get('prompt_label'):
                    label += ' ' + image['prompt_label']
                parts.extend([{'text': label}, {'inlineData': {
                    'mimeType': image['mime_type'], 'data': base64.b64encode(data).decode('ascii')}}])
            endpoint = f'https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent'
            response = self._session.post(endpoint, headers={'x-goog-api-key': self._credential},
                json={'contents': [{'role': 'user', 'parts': parts}], 'generationConfig': config},
                timeout=(15, self.timeout_seconds), allow_redirects=False)
            record['http_status'] = response.status_code
            body = _sanitize(response.json(), self._credential)
            record['provider_response'] = body
            if response.status_code != 200:
                raise RuntimeError('Provider returned a non-success status')
            candidates = body.get('candidates', [])
            if len(candidates) != 1 or candidates[0].get('finishReason') != 'STOP':
                raise RuntimeError('Provider did not return one complete response')
            content = ''.join(v.get('text', '') for v in candidates[0].get('content', {}).get('parts', [])
                              if not v.get('thought'))
            parsed = json.loads(content)
            # The domain caller validates the requested schema; transport never
            # treats arbitrary provider JSON as verified material evidence.
            if not isinstance(parsed, dict):
                raise ValueError('Provider JSON response must be an object')
            record.update(status='complete', response=parsed, response_sha256=_hash(_json(parsed)),
                          usage=body.get('usageMetadata', {}), model_version=body.get('modelVersion'))
        except Exception as error:
            # Error text/requests/headers can contain secrets. Persist only type.
            record.update(status='failed_or_uncertain', error_type=type(error).__name__)
            record['duration_seconds'] = time.monotonic() - started
            self._write(receipt_path, record)
            raise RuntimeError('Semantic inference failed or completion is uncertain; see sanitized receipt; no retry') from None
        record['duration_seconds'] = time.monotonic() - started
        self._write(receipt_path, record)
        return record
