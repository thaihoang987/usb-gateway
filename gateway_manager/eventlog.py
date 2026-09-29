"""Bounded event history shared by workers and retained across restarts."""
import collections
import json
import threading
from datetime import datetime
from pathlib import Path


class EventLog:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.entries = collections.deque(maxlen=2000)
        self.persistence_error = None
        for source in (path.with_suffix('.jsonl.1'), path):
            try:
                with source.open(encoding='utf-8') as stream:
                    for line in stream:
                        try:
                            entry = json.loads(line)
                            if isinstance(entry, dict) and all(k in entry for k in ('time', 'level', 'message')):
                                self.entries.append(entry)
                        except (ValueError, TypeError):
                            pass
            except FileNotFoundError:
                pass
            except OSError as exc:
                self.persistence_error = str(exc)

    def emit(self, level, message, gateway_id='', gateway_name=''):
        entry = dict(time=datetime.now().astimezone().isoformat(timespec='milliseconds'),
                     level=level, message=str(message)[:8192], gateway_id=gateway_id,
                     gateway_name=gateway_name)
        with self.lock:
            self.entries.append(entry)
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if self.path.exists() and self.path.stat().st_size >= 2 * 1024 * 1024:
                    self.path.replace(self.path.with_suffix('.jsonl.1'))
                with self.path.open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps(entry, ensure_ascii=False) + '\n')
                self.persistence_error = None
            except OSError as exc:
                self.persistence_error = str(exc)
        print(f"{entry['time']} [{level}] [{gateway_name or 'system'}] {entry['message']}", flush=True)

    def read(self, limit=500, gateway_id='', level=''):
        with self.lock:
            return [dict(e) for e in self.entries
                    if (not gateway_id or e.get('gateway_id') == gateway_id)
                    and (not level or e['level'] == level)][-max(1, min(limit, 2000)):]
