"""Persistent, content-derived clip IDs shared by filenames, Drive and publishing."""
import hashlib
import json
import os
from pathlib import Path
import re
from drive_helper_json import save_private_json


def checksum(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def clip_code(sha256):
    if not re.fullmatch(r'[0-9a-f]{64}', sha256):
        raise ValueError('Некорректный SHA-256 ролика.')
    return 'CF-' + sha256[:32].upper()


def finish_output(job_dir, index, path, segment):
    """Save a recovery receipt before moving the completed render to its permanent name."""
    job_dir, path = Path(job_dir), Path(path)
    sha = checksum(path)
    code = clip_code(sha)
    name = code + '.mp4'
    state = json.loads((job_dir / 'state.json').read_text())
    receipt = {'file': name, 'sha256': sha, 'id': code,
               'start_time': segment['start_time'], 'end_time': segment['end_time']}
    old = state.get('clip_outputs', {}).get(str(index))
    if old and old['sha256'] != sha:
        raise ValueError('Содержимое уже созданного ролика изменилось. ID сохранён; требуется проверка.')
    state.setdefault('clip_outputs', {})[str(index)] = receipt
    save_private_json(job_dir / 'state.json', state)
    target = job_dir / name
    if target != path:
        if target.exists() and checksum(target) != sha:
            raise ValueError('Конфликт имени ролика.')
        os.replace(path, target)
    return {**segment, **receipt, 'bytes': target.stat().st_size,
            'duration_seconds': segment['end_time'] - segment['start_time']}


def normalize_job(job_dir, metadata=None):
    """Upgrade legacy manifests in place without re-rendering or duplicating Drive files."""
    job_dir = Path(job_dir)
    manifest_path = job_dir / 'manifest.json'
    if not manifest_path.exists():
        return None
    manifest = json.loads(manifest_path.read_text())
    if not manifest.get('clips'):
        return manifest
    state = json.loads((job_dir / 'state.json').read_text())
    moves = []
    for index, clip in enumerate(manifest['clips']):
        old_name = clip['file']
        if Path(old_name).name != old_name:
            raise ValueError('Некорректное имя файла ролика.')
        receipt = state.get('clip_outputs',{}).get(str(index),{})
        old_record = state.get('drive_files', {}).get(old_name) or state.get('drive_files',{}).get(receipt.get('file',''))
        candidate = job_dir / old_name
        known_sha = clip.get('sha256') or receipt.get('sha256')
        # A previous attempt may have moved the file before saving the manifest.
        if not candidate.exists() and known_sha:
            candidate = job_dir / (clip_code(known_sha) + '.mp4')
        sha = checksum(candidate) if candidate.exists() else known_sha
        if sha and known_sha and sha != known_sha:
            raise ValueError('SHA-256 ролика не совпадает с сохранённым паспортом.')
        if not sha and old_record and metadata:
            info = metadata(old_record['id'])
            sha = info.get('sha256Checksum')
        if not sha:
            raise ValueError('Для паспорта ролика нужен файл или проверенный SHA-256 Google Drive.')
        code = clip_code(sha)
        new_name = code + '.mp4'
        if candidate.exists() and candidate.name != new_name:
            moves.append((candidate, job_dir / new_name, sha))
        clip.update(id=code, sha256=sha, file=new_name,
                    duration_seconds=clip['end_time'] - clip['start_time'])
        if old_name != new_name:
            clip.setdefault('original_file', old_name)
            state.setdefault('file_aliases', {})[old_name] = new_name
            for key in ('drive_files', 'telegram_files'):
                mapping = state.get(key, {})
                if old_name in mapping:
                    if new_name in mapping and mapping[new_name] != mapping[old_name]:
                        raise ValueError('Конфликт сохранённых ссылок ролика.')
                    mapping[new_name] = mapping.pop(old_name)
            state['delivered_files'] = [new_name if f == old_name else f
                                        for f in state.get('delivered_files', [])]
        state.setdefault('clip_outputs', {})[str(index)] = {
            k: clip[k] for k in ('file', 'sha256', 'id', 'start_time', 'end_time')}
    # Make legacy aliases durable first, so either manifest version can be recovered.
    save_private_json(job_dir / 'state.json', state)
    for old, new, sha in moves:
        if new.exists() and checksum(new) != sha:
            raise ValueError('Конфликт имени ролика.')
        os.replace(old, new)
    manifest['job_id'] = state['id']
    manifest['identity_version'] = 1
    save_private_json(manifest_path, manifest)
    return manifest
