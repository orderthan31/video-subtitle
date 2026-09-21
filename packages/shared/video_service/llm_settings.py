"""Per-account model preferences and encrypted, write-only API credentials."""
import json
import os
import re
from pathlib import Path
from uuid import uuid4

from cryptography.fernet import Fernet
from .locking import _file_lock
from .storage import read_json


MODEL_FIELDS = ('transcription_model', 'translation_model', 'transcription_fallback_model', 'translation_fallback_model')
FLAGS = ('fallback_on_error', 'fallback_on_block')


class SettingsConflict(ValueError):
    pass


class LLMSettingsStore:
    def __init__(self, root):
        self.directory = Path(root) / '.llm-settings'
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)

    def path(self, owner):
        if owner is not None and not re.fullmatch(r'[a-f0-9]{32}', owner):
            raise ValueError('Invalid settings owner')
        return self.directory / ((owner or 'local') + '.json')

    def _write(self, path, value):
        temp = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
        try:
            with os.fdopen(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
                stream.write(value)
                stream.flush()
                os.fsync(stream.fileno())
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)

    def _cipher(self):
        path = self.directory / 'master.key'
        if not path.exists():
            raise ValueError('Credential encryption key is missing')
        return Fernet(path.read_bytes())

    def _read(self, owner):
        try:
            return read_json(self.path(owner))
        except FileNotFoundError:
            return {'revision': 0}

    def resolve(self, owner=None):
        saved = self._read(owner)
        result = {
            'transcription_model': os.getenv('GEMINI_TRANSCRIPTION_MODEL', 'gemini-3.8-flash'),
            'translation_model': os.getenv('GEMINI_TRANSLATION_MODEL', 'gemini-3.8-flash'),
            'transcription_fallback_model': '', 'translation_fallback_model': '',
            'fallback_on_error': False, 'fallback_on_block': False,
            **{key: saved[key] for key in (*MODEL_FIELDS, *FLAGS) if key in saved},
            'revision': saved['revision'],
        }
        result['api_key'] = self._cipher().decrypt(saved['credential'].encode()).decode() if saved.get('credential') else os.getenv('GEMINI_API_KEY', '')
        result['key_source'] = 'registered' if saved.get('credential') else 'environment' if result['api_key'] else 'none'
        return result

    def public(self, owner=None):
        result = self.resolve(owner)
        result['has_api_key'] = bool(result.pop('api_key'))
        return result

    def update(self, owner, payload):
        if not isinstance(payload, dict) or set(payload) - {*MODEL_FIELDS, *FLAGS, 'revision', 'api_key', 'remove_api_key'}:
            raise ValueError('Invalid settings fields')
        for field in MODEL_FIELDS:
            value = payload.get(field)
            if not isinstance(value, str) or (not value and not field.endswith('fallback_model')) or (value and not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', value)):
                raise ValueError('Invalid model name')
        for field in FLAGS:
            if type(payload.get(field)) is not bool:
                raise ValueError('Invalid fallback policy')
        for stage in ('transcription', 'translation'):
            if payload[stage + '_model'] == payload[stage + '_fallback_model']:
                raise ValueError('Fallback model must differ from primary model')
        key = payload.get('api_key')
        if key is not None and (not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{20,256}', key)):
            raise ValueError('Invalid API key format')
        if type(payload.get('remove_api_key', False)) is not bool or (key and payload.get('remove_api_key')):
            raise ValueError('Invalid key action')
        with _file_lock(self.directory / 'settings.lock'):
            saved = self._read(owner)
            if type(payload.get('revision')) is not int or payload['revision'] != saved['revision']:
                raise SettingsConflict('Settings changed; reload before saving')
            updated = {**saved, **{field: payload[field] for field in (*MODEL_FIELDS, *FLAGS)}, 'revision': saved['revision'] + 1}
            if key:
                master = self.directory / 'master.key'
                if not master.exists():
                    if any(self.directory.glob('*.json')):
                        # Do not silently replace a lost key for existing credentials.
                        if any(read_json(p).get('credential') for p in self.directory.glob('*.json')):
                            raise ValueError('Credential encryption key is missing')
                    self._write(master, Fernet.generate_key())
                updated['credential'] = self._cipher().encrypt(key.encode()).decode()
            if payload.get('remove_api_key'):
                updated.pop('credential', None)
            self._write(self.path(owner), json.dumps(updated).encode())
            return self.public(owner)
