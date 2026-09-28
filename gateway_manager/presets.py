import json
import os
import tempfile
import threading
import uuid
from pathlib import Path
from .config import ConfigError


class PresetStore:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()

    def list(self):
        with self.lock:
            if not self.path.exists():
                return []
            return json.loads(self.path.read_text(encoding='utf-8'))

    def save(self, payload):
        name = payload.get('name', '')
        form = payload.get('form')
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
            raise ConfigError('Preset name must contain 1–100 characters')
        if not isinstance(form, dict) or len(json.dumps(form)) > 20000:
            raise ConfigError('Invalid preset form')
        with self.lock:
            rows = self.list()
            key = payload.get('id')
            if key and not any(row['id'] == key for row in rows):
                raise ConfigError('Preset not found')
            if not key and len(rows) >= 100:
                raise ConfigError('Maximum 100 presets')
            row = {'id': key or uuid.uuid4().hex, 'name': name.strip(), 'form': form}
            rows = [r for r in rows if r['id'] != row['id']] + [row]
            self._write(rows)
            return row

    def delete(self, key):
        with self.lock:
            self._write([r for r in self.list() if r['id'] != key])

    def _write(self, rows):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix='.presets-')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as out:
                json.dump(rows, out, ensure_ascii=False, indent=2)
                out.flush()
                os.fsync(out.fileno())
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
