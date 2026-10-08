"""Versioned ASPIRE examples and scoped learning, separate from writable run state."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import textwrap


DEFAULT_ROOT = Path(__file__).resolve().parents[2] / 'skills' / 'aspire'


def _hash(data):
    return hashlib.sha256(data).hexdigest()


class PublishedSkillLibrary:
    """Read verified source as coding context; never execute or replay a saved plan."""

    def __init__(self, root=DEFAULT_ROOT):
        self.root = Path(root).resolve()
        self.manifest = json.loads((self.root / 'manifest.json').read_text())
        if self.manifest.get('schema_version') != 1:
            raise ValueError('Unsupported published ASPIRE skill catalog')
        self.files = self.manifest['files']
        for relative, expected in self.files.items():
            path = (self.root / relative).resolve()
            if not path.is_relative_to(self.root) or _hash(path.read_bytes()) != expected:
                raise ValueError('Published ASPIRE skill integrity failed: ' + relative)
        self.patterns = self._load('patterns.json')['patterns']
        self._programs = self._load('programs.json')['programs']
        for pattern in self.patterns:
            for snippet in pattern['snippets']:
                source = self._source(snippet['source'])
                if _hash(source.encode()) != snippet['source_sha256']:
                    raise ValueError('Published snippet source hash differs')
                original = textwrap.dedent('\n'.join(source.splitlines()[
                    snippet['start_line'] - 1:snippet['end_line']]))
                if original != snippet['original_code']:
                    raise ValueError('Published snippet differs from its recorded source lines')
                generalized = original
                for old, new in snippet.get('generalization', {}).items():
                    if not old or old not in generalized:
                        raise ValueError('Published snippet generalization has no source match')
                    generalized = generalized.replace(old, new)
                if generalized != snippet['code']:
                    raise ValueError('Published generalized snippet differs')
        for program in self._programs:
            if _hash(self._source(program['source']).encode()) != program['source_sha256']:
                raise ValueError('Published program source hash differs')

    def _load(self, relative):
        if relative not in self.files:
            raise ValueError('Unlisted published skill file')
        return json.loads((self.root / relative).read_text())

    def _source(self, relative):
        if relative not in self.files:
            raise ValueError('Unlisted published skill source')
        return (self.root / relative).read_text()

    @staticmethod
    def _terms(text):
        return set(re.findall(r'[a-z0-9]+', text.lower()))

    def programs(self, task, limit=3):
        terms = self._terms(task)
        ranked = []
        for item in self._programs:
            rank = len(terms & self._terms(item['task'] + ' ' + item.get('lesson', '')))
            ranked.append((rank, item.get('validation') == 'PHYSICAL_SUCCESS', item['source_sha256'], item))
        return [{**item, 'source': self._source(item['source']),
                 'source_path': str(self.root / item['source']), 'origin': 'published_skill_catalog'}
                for _, _, _, item in sorted(ranked, key=lambda x: x[:3], reverse=True)[:limit]]

    def retrieve(self, task, limit=6, *, context=''):
        terms = self._terms(task + ' ' + context)
        ranked = []
        for pattern in self.patterns:
            words = self._terms(' '.join([pattern['topic'], pattern['title'], pattern['trigger'],
                                         ' '.join(pattern.get('keywords', []))]))
            score = len(terms & words)
            if score:
                ranked.append((score, pattern))
        ranked.sort(key=lambda x: (-x[0], x[1]['id']))
        selected, topics = [], set()
        for row in ranked:
            if row[1]['topic'] not in topics and len(selected) < limit:
                selected.append(row)
                topics.add(row[1]['topic'])
        for row in ranked:
            if row not in selected and len(selected) < limit:
                selected.append(row)
        return {'aspire_commit': self.manifest['aspire_commit'],
                'library_sha256': _hash((self.root / 'manifest.json').read_bytes()),
                'semantics': 'Published development recipes and caveats, with original validation scopes. '
                             'Fresh measurements and native full planning are required for every execution.',
                'entries': [item for _, item in selected],
                'failure_findings': [item for _, item in ranked
                    if any(status != 'observed_recipe' for status in item['statuses'])]}


def merge_topic_knowledge(local, published):
    """Current local learning overrides matching published pattern identities."""
    result = dict(local or {})
    known = {entry['id'] for key in ('entries', 'failure_findings')
             for entry in result.get(key, [])}
    for key in ('entries', 'failure_findings'):
        entries = list(result.get(key, []))
        entries.extend(entry for entry in published.get(key, []) if entry['id'] not in known)
        result[key] = entries
    result['published_library_sha256'] = published.get('library_sha256')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    parser.add_argument('--query', help='Retrieve coding context for this task; does not contact a robot')
    args = parser.parse_args()
    library = PublishedSkillLibrary(args.root)
    if args.query:
        output = library.retrieve(args.query)
        output['programs'] = [{k: v for k, v in p.items() if k != 'source'}
                              for p in library.programs(args.query)]
    else:
        output = {'programs': len(library._programs), 'patterns': len(library.patterns),
                  'executable_manifest': str(args.root / 'executable' / 'pick_place.json')}
    print(json.dumps(output, indent=2))


if __name__ == '__main__':
    main()
