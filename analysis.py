from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse


def probe_media(path: Path, expected_duration: float) -> dict:
    result = subprocess.run([
        'ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path),
    ], check=True, capture_output=True, text=True, encoding='utf-8')
    data = json.loads(result.stdout)
    streams = data.get('streams', [])
    if not any(s.get('codec_type') == 'video' for s in streams):
        raise ValueError('missing video stream')
    if not any(s.get('codec_type') == 'audio' for s in streams):
        raise ValueError('missing audio stream')
    duration = float(data.get('format', {}).get('duration', 0))
    if not math.isfinite(duration) or duration <= 0 or abs(duration - expected_duration) > max(2, expected_duration * 0.001):
        raise ValueError('影片時長不符；不能把部分下載當完整原片')
    return data


def probe_audio(path: Path, expected_duration: float) -> dict:
    result = subprocess.run(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)], check=True, capture_output=True, text=True, encoding='utf-8')
    data = json.loads(result.stdout)
    duration = float(data.get('format', {}).get('duration', 0))
    if not any(stream.get('codec_type') == 'audio' for stream in data.get('streams', [])) or not math.isfinite(duration) or abs(duration - expected_duration) > max(2, expected_duration * 0.001):
        raise ValueError('音訊時長不符；可能是中斷的擷取，不接受部分轉錄冒充全片')
    return data


def select_completed_lives(entries: list[dict], limit: int) -> list[dict]:
    if limit < 1:
        raise ValueError('limit 必須大於 0')
    return [entry for entry in entries if entry.get('live_status') == 'was_live'][:limit]


def validate_records(root: Path, video_id: str, duration: float, records: list[dict]) -> list[str]:
    errors = []
    if not records:
        return ['沒有分析紀錄']
    for index, record in enumerate(records):
        label = f'record {index + 1}'
        required = ['asset', 'timeframe', 'speaker_statement', 'visible_facts', 'inference', 'drawing_basis', '成立條件', '失效條件', '後續修正']
        for field in required:
            if not isinstance(record.get(field), str) or not record[field].strip():
                errors.append(f'{label}: 缺少 {field}（無證據請明寫未知）')
        indicators = record.get('indicators')
        if not isinstance(indicators, list) or not indicators or any(not isinstance(item, dict) or not item.get('name') or not item.get('parameters') for item in indicators):
            errors.append(f'{label}: 缺少指標名稱或參數，無證據請明寫未知')
        seconds = record.get('timestamp_seconds')
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or not 0 <= seconds < duration:
            errors.append(f'{label}: 原片時間越界或無效')
            continue
        parsed = urlparse(record.get('source_url', ''))
        query = parse_qs(parsed.query)
        if parsed.scheme != 'https' or parsed.hostname != 'www.youtube.com' or parsed.path != '/watch' or query.get('v') != [video_id] or query.get('t') != [f'{math.floor(seconds)}s']:
            errors.append(f'{label}: 來源或原片時間連結不一致')
        frame = (root / record.get('screenshot', '')).resolve()
        if not frame.is_relative_to(root.resolve()) or not frame.is_file() or frame.stat().st_size == 0:
            errors.append(f'{label}: 截圖不存在或超出證據目錄')
        if not isinstance(record.get('claim'), str) or not record['claim'].strip():
            errors.append(f'{label}: 缺少結論')
    return errors


def validate_segments(duration: float, segments: list[dict]) -> list[str]:
    errors = []
    cursor = 0.0
    if not segments:
        return ['分段未涵蓋原片']
    for segment in segments:
        start, end = segment.get('start'), segment.get('end')
        if not all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) for value in [start, end]):
            return ['分段時間無效']
        if abs(start - cursor) > 0.001 or end <= start or end > duration + 0.001:
            errors.append('分段必須連續涵蓋原片且不能重疊')
        category = segment.get('category')
        if category not in {'stock', 'non_stock', 'uncertain'}:
            errors.append('未知段落分類')
        if category == 'non_stock' and (segment.get('reviewed') is not True or not segment.get('reason')):
            errors.append('未核對或無理由的段落不可排除')
        cursor = end
    if abs(cursor - duration) > 0.001:
        errors.append('分段未涵蓋原片結尾')
    return errors


ROOT = Path(__file__).resolve().parent
CHANNEL = 'https://www.youtube.com/@jinjason999'
CHANNEL_ID = 'UCR7kylWglbT5pF1UXBybHCw'


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def load_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def run_command(command: list[str], log: Path, json_output: bool = False):
    log.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUTF8='1')
    if json_output:
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', env=env)
        log.write_text(result.stderr, encoding='utf-8')
        if result.returncode:
            raise RuntimeError(f'指令失敗，見 {log}\n{result.stderr[-3000:]}')
        return json.loads(result.stdout)
    with log.open('w', encoding='utf-8') as output:
        result = subprocess.run(command, stdout=output, stderr=output, env=env)
    if result.returncode:
        raise RuntimeError(f'指令失敗，見 {log}\n{log.read_text(encoding="utf-8", errors="replace")[-3000:]}')


def yt_dlp_args() -> list[str]:
    arguments = ['yt-dlp', '--ignore-config', '--no-playlist', '--no-progress', '--retries', '3', '--socket-timeout', '30']
    if shutil.which('node'):
        arguments += ['--js-runtimes', 'node']
    return arguments


def video_id_from_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.hostname not in {'www.youtube.com', 'youtube.com', 'youtu.be'}:
        raise ValueError('只接受公開 YouTube HTTPS 影片連結')
    video_id = parsed.path.strip('/') if parsed.hostname == 'youtu.be' else parse_qs(parsed.query).get('v', [''])[0]
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
        raise ValueError('無效影片 ID')
    return video_id


def source_link(video_id: str, seconds: float) -> str:
    return f'https://www.youtube.com/watch?v={video_id}&t={math.floor(seconds)}s'


def discover(root: Path, limit: int) -> dict:
    if limit < 1:
        raise ValueError('lives 必須大於 0')
    listing = run_command(yt_dlp_args() + [
        '--flat-playlist', '--playlist-end', str(limit + 5), '--dump-single-json', CHANNEL + '/streams',
    ], root / 'logs/discover.log', json_output=True)
    selected = select_completed_lives(listing.get('entries', []), limit)
    result = {'channel': CHANNEL, 'selected': selected, 'requested': limit, 'captured_at': datetime.now(timezone.utc).isoformat()}
    save_json(root / 'data/discovery.json', result)
    return {'selected': [{'id': e['id'], 'title': e.get('title'), 'duration': e.get('duration')} for e in selected], 'requested': limit}


def watch_helper(scripts: str | None, name: str):
    location = scripts or os.environ.get('WATCH_SCRIPTS')
    if not location:
        candidates = sorted((Path.home() / '.claude/plugins/cache/claude-video/watch').glob('*/skills/watch/scripts'))
        location = str(candidates[-1]) if candidates else ''
    path = Path(location) / f'{name}.py'
    if not location or not path.is_file():
        raise RuntimeError('找不到既有 /watch helper；請指定 --watch-scripts，或保留待轉錄狀態')
    spec = importlib.util.spec_from_file_location(f'market_watch_{name}', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, path


def transcribe_chinese(audio: Path, scripts: str | None) -> dict:
    import requests

    if audio.stat().st_size > 24 * 1024**2:
        raise ValueError('單片驗證音訊超過 24 MiB；需另做分塊並保留失敗區段，不自動截斷')
    key = os.environ.get('GROQ_API_KEY')
    if not key:
        helper, _ = watch_helper(scripts, 'whisper')
        _, key = helper.load_api_key('groq')
    if not key:
        raise RuntimeError('既有 Groq 金鑰不可用；保留待轉錄，不更動設定')
    with audio.open('rb') as source:
        response = requests.post(
            'https://api.groq.com/openai/v1/audio/transcriptions',
            headers={'Authorization': f'Bearer {key}'},
            files={'file': (audio.name, source, 'audio/mpeg')},
            data={'model': 'whisper-large-v3', 'language': 'zh', 'response_format': 'verbose_json', 'temperature': '0', 'prompt': '以下是臺灣股票節目，以繁體中文逐字轉錄，保留數字、股票代碼、指標名稱與原話，不翻譯。'},
            timeout=(30, 300),
        )
    if not response.ok:
        raise RuntimeError(f'Groq 語音辨識失敗 HTTP {response.status_code}；未標完成')
    return response.json()


def capture_frames(root: Path, video_id: str, times: list[float]) -> dict:
    folder = root / 'data' / video_id
    manifest = load_json(folder / 'manifest.json')
    index_path = folder / 'frames.json'
    frames = load_json(index_path) if index_path.exists() else []
    for seconds in times:
        if not math.isfinite(seconds) or not 0 <= seconds < manifest['duration_seconds']:
            raise ValueError('截圖時間超出原片')
        milliseconds = round(seconds * 1000)
        relative = f'frames/{manifest["media_sha256"][:12]}/t_{milliseconds:010d}.jpg'
        output = folder / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        if not output.exists():
            run_command([
                'ffmpeg', '-hide_banner', '-loglevel', 'error', '-n', '-ss', str(seconds),
                '-i', str(folder / manifest['media']), '-frames:v', '1', '-pix_fmt', 'yuvj420p', '-threads', '1', '-q:v', '2', str(output),
            ], root / f'logs/{video_id}-frame-{milliseconds}.log')
        if not output.is_file() or not output.stat().st_size:
            raise RuntimeError('未產出有效截圖')
        subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-i', str(output), '-f', 'null', '-'], check=True, capture_output=True)
        if not any(f['screenshot'] == relative for f in frames):
            frames.append({'timestamp_seconds': seconds, 'screenshot': relative, 'source_url': source_link(video_id, seconds), 'media_sha256': manifest['media_sha256'], 'screenshot_sha256': hashlib.sha256(output.read_bytes()).hexdigest(), 'method': 'ffmpeg accurate input seek; first frame at or after target, within one frame interval'})
    frames.sort(key=lambda f: f['timestamp_seconds'])
    save_json(index_path, frames)
    return {'video_id': video_id, 'frames': len(frames)}


def sample(root: Path, url: str, transcribe: bool, scripts: str | None) -> dict:
    video_id = video_id_from_url(url)
    folder = root / 'data' / video_id
    folder.mkdir(parents=True, exist_ok=True)
    metadata_path = folder / 'source.info.json'
    canonical = f'https://www.youtube.com/watch?v={video_id}'
    if not metadata_path.exists():
        info = run_command(yt_dlp_args() + ['--skip-download', '--dump-single-json', canonical], root / f'logs/{video_id}-metadata.log', json_output=True)
        save_json(metadata_path, info)
    info = load_json(metadata_path)
    if info.get('id') != video_id or info.get('channel_id') != CHANNEL_ID or info.get('live_status') != 'was_live':
        raise ValueError('只處理指定頻道已結束的直播；不接受進行中、待處理或未確認影片')
    media = next((folder / ('source' + ext) for ext in ['.mkv', '.mp4', '.webm'] if (folder / ('source' + ext)).exists()), None)
    if media is None:
        if shutil.disk_usage(folder).free < 5 * 1024**3:
            raise RuntimeError('可用空間不足 5 GiB；未開始下載')
        run_command(yt_dlp_args() + [
            '--format', 'bv[vcodec^=avc1][height<=1080]+ba[language^=zh]/bv[height<=1080]+ba[language^=zh]', '--merge-output-format', 'mkv',
            '--max-filesize', '2G', '--concurrent-fragments', '2', '--write-info-json',
            '-o', str(folder / 'source.%(ext)s'), canonical,
        ], root / f'logs/{video_id}-download.log')
        media = next((folder / ('source' + ext) for ext in ['.mkv', '.mp4', '.webm'] if (folder / ('source' + ext)).exists()), None)
    if media is None:
        raise RuntimeError('未產出原片；不視為成功')
    probe = probe_media(media, float(info['duration']))
    save_json(folder / 'probe.json', probe)
    duration = float(probe['format']['duration'])
    with media.open('rb') as source_file:
        media_hash = hashlib.file_digest(source_file, 'sha256').hexdigest()
    manifest_path = folder / 'manifest.json'
    manifest = load_json(manifest_path) if manifest_path.exists() else {}
    manifest.update({
        'video_id': video_id, 'title': info.get('title'), 'source_url': canonical,
        'channel_id': info['channel_id'], 'live_status': info['live_status'],
        'duration_seconds': duration, 'metadata_duration_seconds': info['duration'],
        'media': media.name, 'download_complete': True, 'audio_video_verified': True,
        'media_sha256': media_hash,
        'transcript_status': manifest.get('transcript_status', 'pending'),
        'analysis_status': 'not_full_reviewed',
    })
    save_json(manifest_path, manifest)
    segments_path = folder / 'segments.json'
    if not segments_path.exists():
        save_json(segments_path, [{'start': 0, 'end': duration, 'category': 'uncertain', 'reviewed': False, 'reason': '尚未完成全片逐段影音核對；不得刪除'}])
    audio_path = folder / 'audio.mp3'
    if not audio_path.exists():
        run_command([
            'ffmpeg', '-hide_banner', '-loglevel', 'error', '-n', '-i', str(media), '-vn',
            '-acodec', 'libmp3lame', '-ar', '16000', '-ac', '1', '-b:a', '64k', str(audio_path),
        ], root / f'logs/{video_id}-audio.log')
    save_json(folder / 'audio.probe.json', probe_audio(audio_path, duration))
    with audio_path.open('rb') as audio_file:
        manifest['audio_sha256'] = hashlib.file_digest(audio_file, 'sha256').hexdigest()
    save_json(manifest_path, manifest)
    transcript_path = folder / 'transcript.json'
    if transcribe and not transcript_path.exists():
        captions = info.get('subtitles') or {}
        automatic = info.get('automatic_captions') or {}
        chinese = [language for language in dict(captions, **automatic) if language.startswith('zh')]
        if chinese:
            language = next((language for language in ['zh-Hant', 'zh-TW', 'zh', 'zh-Hans'] if language in chinese), chinese[0])
            run_command(yt_dlp_args() + [
                '--skip-download', '--write-subs', '--write-auto-subs', '--sub-langs', language,
                '--sub-format', 'vtt', '-o', str(folder / 'source.%(ext)s'), canonical,
            ], root / f'logs/{video_id}-captions.log')
            parser, helper_path = watch_helper(scripts, 'transcribe')
            cues = parser.parse_vtt(str(folder / f'source.{language}.vtt'))
            transcript = {'source_video_id': video_id, 'source': 'youtube_captions', 'language': language, 'helper': str(helper_path), 'segments': cues}
        else:
            raw = transcribe_chinese(audio_path, scripts)
            save_json(folder / 'transcript.zh.raw.json', raw)
            cues = [{'start': cue['start'], 'end': cue['end'], 'text': cue['text'].strip()} for cue in raw.get('segments', []) if cue.get('text', '').strip()]
            transcript = {'source_video_id': video_id, 'source': 'whisper_groq', 'requested_language': 'zh', 'machine_generated': True, 'segments': cues}
        if not cues or any(not 0 <= cue['start'] < cue['end'] <= duration + 1 for cue in cues):
            raise ValueError('轉錄缺少有效原片時間；不視為完成')
        save_json(transcript_path, transcript)
        manifest['transcript_status'] = 'machine_generated_requires_review'
        save_json(manifest_path, manifest)
    if transcript_path.exists():
        transcript = load_json(transcript_path)
        cues = transcript.get('segments', [])
        if transcript.get('source_video_id') != video_id or not cues or any(not 0 <= cue['start'] < cue['end'] <= duration + 1 for cue in cues):
            raise ValueError('既有轉錄的來源或時間無效')
        manifest['transcript_status'] = 'machine_generated_requires_review'
        save_json(manifest_path, manifest)
        text = '\n'.join(f"[{cue['start']:.2f}–{cue['end']:.2f}] {cue['text']}" for cue in cues)
        (folder / 'transcript.txt').write_text(text + '\n', encoding='utf-8')
    capture_frames(root, video_id, [min(30, duration * 0.05), duration / 4, duration / 2, duration * 0.7, duration * 0.85])
    return manifest


def progress(root: Path) -> dict:
    items = []
    for path in sorted((root / 'data').glob('*/manifest.json')):
        manifest = load_json(path)
        records_path = path.parent / 'records.json'
        records = load_json(records_path) if records_path.exists() else []
        items.append({'video_id': manifest['video_id'], 'download_complete': manifest.get('download_complete', False), 'transcript_status': manifest.get('transcript_status'), 'evidence_records': len(records), 'full_review_completed': False})
    return {'videos': items, 'downloaded': sum(item['download_complete'] for item in items), 'full_analyses_completed': 0, 'livestream_target': 20}


def validate(root: Path, video_id: str) -> dict:
    folder = root / 'data' / video_id
    manifest = load_json(folder / 'manifest.json')
    records = load_json(folder / 'records.json')
    media = folder / manifest['media']
    probe_media(media, manifest['metadata_duration_seconds'])
    with media.open('rb') as source:
        if hashlib.file_digest(source, 'sha256').hexdigest() != manifest['media_sha256']:
            raise ValueError('原片雜湊改變；舊證據失效')
    audio = folder / 'audio.mp3'
    probe_audio(audio, manifest['duration_seconds'])
    with audio.open('rb') as source:
        if hashlib.file_digest(source, 'sha256').hexdigest() != manifest.get('audio_sha256'):
            raise ValueError('音訊雜湊不符')
    transcript = load_json(folder / 'transcript.json')
    cues = transcript.get('segments', [])
    if transcript.get('source_video_id') != video_id or not cues or any(not 0 <= cue['start'] < cue['end'] <= manifest['duration_seconds'] + 1 for cue in cues):
        raise ValueError('缺少有效的原片時間轉錄')
    errors = validate_records(folder, video_id, manifest['duration_seconds'], records)
    errors.extend(validate_segments(manifest['duration_seconds'], load_json(folder / 'segments.json')))
    frames = load_json(folder / 'frames.json')
    for record in records:
        matches = [frame for frame in frames if frame['screenshot'] == record.get('screenshot') and frame['timestamp_seconds'] == record.get('timestamp_seconds')]
        if not matches or matches[0].get('media_sha256') != manifest['media_sha256']:
            errors.append('分析紀錄未對應實際擷取時間索引或原片版本')
            continue
        image = (folder / record['screenshot']).resolve()
        if not image.is_relative_to(folder.resolve()) or not image.is_file():
            continue
        if hashlib.sha256(image.read_bytes()).hexdigest() != matches[0].get('screenshot_sha256'):
            errors.append('截圖已被更動')
        subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-i', str(image), '-f', 'null', '-'], check=True, capture_output=True)
    return {'valid': not errors, 'scope': '原片及音訊完整性、版本、轉錄時間、必要欄位、截圖解碼及索引；不驗內容正確性或語音辨識準確度', 'records': len(records), 'errors': errors}


def main() -> int:
    parser = argparse.ArgumentParser(description='Market--Ana 單場直播證據工具（不自動交易、不發布）')
    parser.add_argument('--root', type=Path, default=ROOT)
    commands = parser.add_subparsers(dest='command', required=True)
    discovery = commands.add_parser('discover')
    discovery.add_argument('--lives', type=int, default=1)
    sampling = commands.add_parser('sample')
    sampling.add_argument('--url', required=True)
    sampling.add_argument('--transcribe', action='store_true', help='無字幕時沿用既有 /watch Whisper 設定，上傳公開影片音訊至該服務')
    sampling.add_argument('--watch-scripts')
    capture = commands.add_parser('capture')
    capture.add_argument('--video-id', required=True)
    capture.add_argument('--timestamps', required=True, help='原片秒數，逗號分隔')
    validation = commands.add_parser('validate')
    validation.add_argument('--video-id', required=True)
    commands.add_parser('progress')
    args = parser.parse_args()
    root = args.root.resolve()
    if args.command in {'capture', 'validate'} and not re.fullmatch(r'[A-Za-z0-9_-]{11}', args.video_id):
        parser.error('無效影片 ID')
    try:
        if args.command == 'discover':
            result = discover(root, args.lives)
        elif args.command == 'sample':
            result = sample(root, args.url, args.transcribe, args.watch_scripts)
        elif args.command == 'capture':
            result = capture_frames(root, args.video_id, [float(value) for value in args.timestamps.split(',')])
        elif args.command == 'validate':
            result = validate(root, args.video_id)
        else:
            result = progress(root)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result.get('valid') is False else 0
    except (ValueError, RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(f'失敗（原件保留）：{error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
