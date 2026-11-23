"""Targeted, locally verified Git-history transforms; no production identifiers.

Private preflight knows the literal markers. CI reproduces only manifest-approved
JSON transforms using hashed identities, and must match every resulting blob SHA.
"""
from __future__ import annotations
import argparse
import hashlib
import html
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

TEXT_SUFFIXES = (b'.md', b'.txt', b'.log', b'.csv', b'.html', b'.xml', b'.php', b'.js', b'.mjs', b'.py', b'.yml', b'.yaml', b'.sh', b'.svg')
TOKEN = re.compile(r'(?<![a-z0-9])([a-z]{2}-[0-9]+)(?![a-z0-9])', re.I)
URL = re.compile(r'https?://[^\s\"\'<>]+', re.I)


def blob_sha(content):
    return hashlib.sha1(b'blob ' + str(len(content)).encode() + b'\0' + content).hexdigest()


def normalized_text(text):
    return html.unescape(unquote(unquote(text)))


def hashed_reference(text, hashes):
    decoded = normalized_text(text)
    for match in TOKEN.finditer(decoded):
        if hashlib.sha256(match.group(1).lower().encode()).hexdigest() in hashes:
            return True
    if 'zvg_id' not in decoded.lower():
        return False
    # Also handle query-only strings and HTML-escaped URLs inside prose.
    candidates = URL.findall(decoded)
    if not candidates:
        candidates = [decoded]
    for candidate in candidates:
        try:
            query = parse_qs(urlsplit(candidate).query)
        except ValueError:
            continue
        for land in query.get('land_abk', []):
            for number in query.get('zvg_id', []):
                token = f'{land}-{number}'.strip().lower()
                if hashlib.sha256(token.encode()).hexdigest() in hashes:
                    return True
    return False


class PrivateDetector:
    """Private-only marker check with literal anchors before narrow regex checks."""
    def __init__(self, config):
        self.oid = config['object_id'].strip().lower()
        self.land, self.number = self.oid.split('-', 1)
        markers = config.get('private_markers', {})
        self.checks = [(self.oid, re.compile(r'(?<![a-z0-9])' + re.escape(self.oid) + r'(?![a-z0-9])', re.I), False)]
        for marker in markers.get('text', []):
            tokens = re.split(r'[\s\-\u2010-\u2015]+', marker.strip())
            if tokens and tokens[0]:
                expression = r'[\s\-\u2010-\u2015]*'.join(re.escape(x) for x in tokens)
                self.checks.append((max(tokens, key=len).lower(), re.compile(expression, re.I), False))
        for marker in markers.get('case_numbers', []):
            tokens = re.findall(r'\d+|[A-Za-z]+|[^\w\s]', marker)
            numbers = [x for x in tokens if x.isdigit()]
            anchor = numbers[-2] if len(numbers) > 1 else max(tokens, key=len)
            expression = r'(?<![a-z0-9])' + r'\s*'.join(re.escape(x) for x in tokens) + r'(?![a-z0-9])'
            self.checks.append((anchor.lower(), re.compile(expression, re.I), anchor.isdigit()))
        self.ascii_only = all(anchor.isascii() for anchor, _, _ in self.checks)
        self.byte_checks = []
        if self.ascii_only:
            for anchor, expression, numeric in self.checks:
                pattern = expression.pattern.replace(r'[\s\-\u2010-\u2015]*', r'(?:[\s-]|\xe2\x80[\x90-\x95]|\xc2\xa0)*').replace(r'\s*', r'(?:\s|\xc2\xa0)*')
                self.byte_checks.append((anchor.encode(), re.compile(pattern.encode(), re.I), numeric))
        self.document_ids = []
        for marker in markers.get('document_ids', []):
            if not re.fullmatch(r'[0-9]+', str(marker)):
                raise ValueError('Document marker must be numeric')
            self.document_ids.append(str(marker))
        land = re.escape(self.land)
        number = re.escape(self.number)
        tail = r"[^\s\"'<>]*?"
        self.query = re.compile(
            r"land_abk=" + land + r"(?=[&#\"'\s])" + tail + r'&zvg_id=' + number + r'(?![0-9])'
            + r"|zvg_id=" + number + r"(?=[&#\"'\s])" + tail + r'&land_abk=' + land + r'(?![a-z0-9])', re.I)

    @staticmethod
    def candidates(lower, anchor, numeric=False):
        position = 0
        while True:
            position = lower.find(anchor, position)
            if position < 0:
                return
            end = position + len(anchor)
            if not numeric or ((position == 0 or not (48 <= lower[position-1] <= 57 if isinstance(lower, bytes) else lower[position-1].isdigit())) and (end == len(lower) or not (48 <= lower[end] <= 57 if isinstance(lower, bytes) else lower[end].isdigit()))):
                yield position
            position = end

    def matches_ascii(self, content, include_documents=True):
        lower = content.lower()
        for anchor, expression, numeric in self.byte_checks:
            for position in self.candidates(lower, anchor, numeric):
                start = max(0, position-512)
                end = min(len(content), position+len(anchor)+512)
                if expression.search(content, start, end):
                    return True
        if include_documents:
            for marker in self.document_ids:
                if next(self.candidates(lower, marker.encode(), True), None) is not None:
                    return True
        if ('zvg_id=' + self.number).encode() in lower:
            return bool(self.query.search(content.decode('utf-8', errors='replace')))
        return False

    def matches_json(self, payload, rendered):
        if not self.ascii_only:
            return self.matches(rendered)
        if self.matches_ascii(rendered, include_documents=False):
            return True
        document_candidate = any(marker.encode() in rendered for marker in self.document_ids)
        encoded_candidate = any(marker in rendered for marker in (b'%', b'&#', b'&amp;'))
        if not document_candidate and not encoded_candidate:
            return False
        # Document numbers in measurements are not identities. Check string
        # references and explicit numeric identifier fields, retaining values.
        identifier_keys = {'id', 'file_id', 'document_id', 'attachment_id', 'file_ids', 'document_ids', 'attachment_ids', 'new_report_ids'}
        stack = [(None, payload)]
        while stack:
            key, value = stack.pop()
            if isinstance(value, dict):
                stack.extend((None, name) for name in value.keys())
                stack.extend(value.items())
            elif isinstance(value, list):
                stack.extend((key, item) for item in value)
            elif isinstance(value, str):
                if document_candidate and any(next(self.candidates(value, marker, True), None) is not None for marker in self.document_ids):
                    return True
                if encoded_candidate and any(marker in value for marker in ('%', '&#', '&amp;')):
                    decoded = normalized_text(value)
                    if decoded != value and self.matches_ascii(decoded.encode('utf-8')):
                        return True
            elif document_candidate and isinstance(value, int) and key in identifier_keys:
                if str(value) in self.document_ids:
                    return True
        return False

    def matches(self, content):
        text = content.decode('utf-8', errors='replace') if isinstance(content, bytes) else content
        decoded = normalized_text(text)
        lower = decoded.lower()
        for anchor, expression, numeric in self.checks:
            for position in self.candidates(lower, anchor, numeric):
                # Full expressions are only evaluated around literal candidates.
                # Expand across long whitespace so unusual wrapping is not missed.
                start = max(0, position-512)
                end = min(len(decoded), position+len(anchor)+512)
                while start > 0 and decoded[start].isspace():
                    start -= 1
                while end < len(decoded) and decoded[end-1].isspace():
                    end += 1
                if expression.search(decoded, start, end):
                    return True
        for marker in self.document_ids:
            if next(self.candidates(lower, marker, True), None) is not None:
                return True
        return f'zvg_id={self.number}' in lower and bool(self.query.search(decoded))


class HistoryCleaner:
    def __init__(self, config, policy_path, manifest=None, audit_path=None):
        self.config = config
        hashes = config.get('id_hashes', [])
        if config.get('object_id'):
            oid = config['object_id'].strip().lower()
            if not re.fullmatch(r'[a-z]{2}-[0-9]+', oid):
                raise ValueError('Invalid object identifier')
            digest = hashlib.sha256(oid.encode()).hexdigest()
            if hashes and hashes != [digest]:
                raise ValueError('Private identifier and approved hash disagree')
            hashes = [digest]
        if not hashes or any(not re.fullmatch(r'[0-9a-f]{64}', x) for x in hashes):
            raise ValueError('Explicit withdrawal hashes are required')
        self.hashes = frozenset(hashes)
        self.blob_ids = frozenset(config.get('blob_ids', []))
        if not self.blob_ids or any(not re.fullmatch(r'[0-9a-f]{40}', x) for x in self.blob_ids):
            raise ValueError('Explicit binary blob IDs are required')
        spec = importlib.util.spec_from_file_location('scoped_history_policy', str(policy_path))
        self.policy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.policy)
        self.policy.WITHDRAWN_ID_HASHES = self.hashes
        self.policy.withdrawn_reference = lambda text: hashed_reference(text, self.hashes)
        self.detector = PrivateDetector(config) if config.get('object_id') else None
        self.manifest = manifest
        self.audit_path = Path(audit_path) if audit_path else None
        self.seen = set()
        if manifest is not None:
            if config.get('object_id') or config.get('private_markers'):
                raise ValueError('CI configuration must contain only hashes')
            if manifest.get('version') != 1 or set(manifest['id_hashes']) != self.hashes or set(manifest['blob_ids']) != self.blob_ids:
                raise ValueError('Manifest scope mismatch')
            if not isinstance(manifest.get('json_blobs'), dict):
                raise ValueError('Missing JSON manifest')
        elif self.detector is None:
            raise ValueError('Private preflight requires object_id; CI requires a manifest')

    def withdrawn_path(self, filename):
        return hashed_reference(filename.decode('utf-8', errors='strict'), self.hashes)

    def fast_transform(self, payload):
        if not isinstance(payload, dict) or not isinstance(payload.get('results'), list):
            return self.policy.sanitize_payload(payload)
        results = [row for row in payload['results'] if not self.policy.withdrawn_record(row)]
        rest = self.policy.sanitize_payload({k: v for k, v in payload.items() if k != 'results'})
        cleaned = {k: results if k == 'results' else rest[k] for k in payload if k == 'results' or k in rest}
        if len(results) != len(payload['results']) and isinstance(cleaned.get('meta'), dict) and 'count' in cleaned['meta']:
            cleaned['meta']['count'] = len(results)
        return cleaned

    @staticmethod
    def serialize(value):
        return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')

    def transform_json(self, old_sha, raw):
        if blob_sha(raw) != old_sha:
            raise ValueError('Input Git blob SHA mismatch')
        entry = None
        if self.manifest is not None:
            entry = self.manifest['json_blobs'].get(old_sha)
            if entry is None:
                raise ValueError('Unreviewed JSON blob encountered: ' + old_sha)
            mode = entry['mode']
            if mode not in ('fast', 'full'):
                raise ValueError('Invalid manifest mode')
        else:
            mode = 'fast'
        payload = json.loads(raw)
        cleaned = self.fast_transform(payload) if mode == 'fast' else self.policy.sanitize_payload(payload)
        rendered = self.serialize(cleaned)
        if self.detector is not None and self.detector.matches_json(cleaned, rendered):
            mode = 'full'
            cleaned = self.policy.sanitize_payload(payload)
            rendered = self.serialize(cleaned)
            if self.detector.matches_json(cleaned, rendered):
                # Report only object hashes; private markers must not reach logs.
                raise ValueError('Remaining private marker requires review in JSON blob ' + old_sha)
        changed = cleaned != payload
        result = rendered if changed else raw
        actual = {'new_sha': blob_sha(result), 'mode': mode, 'changed': changed}
        if entry is not None and any(actual[k] != entry[k] for k in actual):
            raise ValueError('Verified JSON transform does not match manifest for ' + old_sha)
        if self.audit_path is not None and old_sha not in self.seen:
            with self.audit_path.open('a', encoding='utf-8') as f:
                f.write(json.dumps({'old_sha': old_sha, **actual}, sort_keys=True) + '\n')
        self.seen.add(old_sha)
        return result, actual

    def inspect_text(self, filename, raw):
        if self.detector is not None:
            bad = self.detector.matches(raw)
        else:
            bad = hashed_reference(raw.decode('utf-8', errors='replace'), self.hashes)
        if bad:
            raise ValueError('Additional textual withdrawal reference needs review in blob ' + blob_sha(raw))


def cleaner_from_environment():
    config = json.loads(os.environ['ZVG_WITHDRAWAL_CONFIG'])
    path = os.environ.get('ZVG_REWRITE_MANIFEST')
    manifest = json.loads(Path(path).read_text()) if path else None
    return HistoryCleaner(config, os.environ['ZVG_POLICY_MODULE'], manifest, os.environ.get('ZVG_REWRITE_AUDIT_PATH'))


class GitBatch:
    def __init__(self, repo):
        self.process = subprocess.Popen(['git', '-C', str(repo), 'cat-file', '--batch'], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    def read(self, sha):
        p = self.process
        p.stdin.write((sha + '\n').encode()); p.stdin.flush()
        header = p.stdout.readline().split()
        if len(header) != 3 or header[0].decode() != sha:
            raise ValueError('Git object unavailable: ' + sha)
        raw = p.stdout.read(int(header[2]))
        if p.stdout.read(1) != b'\n':
            raise ValueError('Invalid Git object framing')
        return header[1].decode(), raw
    def close(self):
        self.process.stdin.close()
        status = self.process.wait()
        self.process.stdout.close()
        if status != 0:
            raise RuntimeError('Git object reader failed')


def verify_audit(manifest_path, audit_path):
    manifest = json.loads(Path(manifest_path).read_text())
    seen = {}
    for line in Path(audit_path).read_text().splitlines():
        row = json.loads(line); old = row.pop('old_sha')
        if old in seen and seen[old] != row:
            raise ValueError('Conflicting callback audit entries')
        seen[old] = row
    expected = manifest['json_blobs']
    if seen != expected:
        raise ValueError('Manifest coverage mismatch: missing=%d extra=%d altered=%d' %
                         (len(set(expected)-set(seen)), len(set(seen)-set(expected)), sum(seen[k] != expected[k] for k in set(seen)&set(expected))))
    return {'ok': True, 'json_blobs_verified': len(seen)}


def prepare_manifest(repo, paths_file, policy_path, config, output):
    cleaner = HistoryCleaner(config, policy_path)
    paths = json.loads(Path(paths_file).read_text())
    manifest = {'version': 1, 'id_hashes': sorted(cleaner.hashes), 'blob_ids': sorted(cleaner.blob_ids), 'json_blobs': {}}
    batch = GitBatch(repo); started = time.monotonic(); text_count = 0
    try:
        for index, (sha, names) in enumerate(paths.items()):
            names = [names] if isinstance(names, str) else names
            kind, raw = batch.read(sha)
            if kind != 'blob':
                raise ValueError('Non-blob in blob inventory')
            active_names = [name for name in names if not cleaner.withdrawn_path(name.encode())]
            if sha in cleaner.blob_ids or not active_names:
                continue
            if any(name.lower().endswith('.json') for name in active_names):
                _, entry = cleaner.transform_json(sha, raw)
                manifest['json_blobs'][sha] = entry
            if any(not name.lower().endswith('.json') for name in active_names):
                cleaner.inspect_text(active_names[0].encode(), raw); text_count += 1
            if (index+1) % 100 == 0:
                print(json.dumps({'checked_blobs': index+1, 'json_blobs': len(manifest['json_blobs']), 'elapsed_seconds': round(time.monotonic()-started)}), flush=True)
    finally:
        batch.close()
    commits = subprocess.check_output(['git','-C',str(repo),'rev-list','--all'],text=True).splitlines()
    tags = subprocess.check_output(['git','-C',str(repo),'for-each-ref','--format=%(objectname) %(objecttype)','refs/tags'],text=True).splitlines()
    objects = commits + [line.split()[0] for line in tags if line.endswith(' tag')]
    batch = GitBatch(repo)
    try:
        for sha in dict.fromkeys(objects):
            kind, raw = batch.read(sha)
            if kind not in ('commit','tag'):
                raise ValueError('Invalid message object')
            cleaner.inspect_text(b'commit-or-tag-message', raw.split(b'\n\n',1)[-1])
    finally:
        batch.close()
    target = Path(output)
    temporary = target.with_name(target.name + '.tmp')
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    temporary.replace(target)
    print(json.dumps({'ok':True,'json_blobs':len(manifest['json_blobs']),'changed_json_blobs':sum(x['changed'] for x in manifest['json_blobs'].values()),'other_text_blobs':text_count,'commit_tag_messages':len(set(objects)),'elapsed_seconds':round(time.monotonic()-started)}),flush=True)


def main():
    parser=argparse.ArgumentParser()
    sub=parser.add_subparsers(dest='command',required=True)
    prep=sub.add_parser('prepare-manifest');prep.add_argument('--repo',required=True);prep.add_argument('--blob-paths',required=True);prep.add_argument('--policy',required=True);prep.add_argument('--config-file',required=True);prep.add_argument('--out',required=True)
    verify=sub.add_parser('verify-audit');verify.add_argument('--manifest',required=True);verify.add_argument('--audit',required=True)
    args=parser.parse_args()
    if args.command=='prepare-manifest':
        prepare_manifest(args.repo,args.blob_paths,args.policy,json.loads(Path(args.config_file).read_text()),args.out)
    else:
        print(json.dumps(verify_audit(args.manifest,args.audit)))


if __name__=='__main__':
    main()
