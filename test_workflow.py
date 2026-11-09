import json
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from analysis import probe_media, select_completed_lives, transcribe_chinese, validate_records, validate_segments


class EvidenceTests(unittest.TestCase):
    def test_validate_cli_rejects_missing_source_even_when_json_and_frame_exist(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / 'data/G4vcmeBFDm0'
            folder.mkdir(parents=True)
            (folder / 'frame.jpg').write_bytes(b'fixture')
            files = {
                'manifest.json': {'video_id': 'G4vcmeBFDm0', 'duration_seconds': 100, 'metadata_duration_seconds': 100, 'media': 'missing.mkv', 'media_sha256': 'missing'},
                'records.json': [{'claim': 'fixture', 'source_url': 'https://www.youtube.com/watch?v=G4vcmeBFDm0&t=1s', 'timestamp_seconds': 1, 'screenshot': 'frame.jpg', 'asset': 'fixture', 'timeframe': '未知', 'indicators': [{'name': '未知', 'parameters': '未知'}], 'speaker_statement': '未知', 'visible_facts': 'fixture', 'inference': '無', 'drawing_basis': '未知', '成立條件': '未知', '失效條件': '未知', '後續修正': '未知'}],
                'frames.json': [{'timestamp_seconds': 1, 'screenshot': 'frame.jpg'}],
                'segments.json': [{'start': 0, 'end': 100, 'category': 'uncertain', 'reviewed': False}],
            }
            for name, value in files.items():
                (folder / name).write_text(json.dumps(value), encoding='utf-8')
            result = subprocess.run([sys.executable, str(Path(__file__).parent / 'analysis.py'), '--root', str(root), 'validate', '--video-id', 'G4vcmeBFDm0'], capture_output=True, text=True, encoding='utf-8')
            self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_record_without_chart_and_condition_fields_cannot_pass_as_complete_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'frame.jpg').write_bytes(b'fixture')
            records = [{'claim': 'incomplete', 'source_url': 'https://www.youtube.com/watch?v=G4vcmeBFDm0&t=1s', 'timestamp_seconds': 1, 'screenshot': 'frame.jpg'}]
            self.assertTrue(validate_records(root, 'G4vcmeBFDm0', 100, records))

    def test_transcription_explicitly_requests_chinese_segment_timestamps(self):
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / 'audio.mp3'
            audio.write_bytes(b'synthetic audio fixture')
            with patch.dict('os.environ', {'GROQ_API_KEY': 'test-only-placeholder'}), patch('requests.post') as post:
                post.return_value.json.return_value = {'segments': [{'start': 0, 'end': 1, 'text': '測試'}]}
                result = transcribe_chinese(audio, None)
                self.assertEqual(post.call_args.kwargs['data']['language'], 'zh')
                self.assertEqual(post.call_args.kwargs['data']['response_format'], 'verbose_json')
                self.assertEqual(result['segments'][0]['text'], '測試')

    def test_sample_cli_resumes_existing_media_and_transcript_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / 'data/G4vcmeBFDm0'
            folder.mkdir(parents=True)
            subprocess.run([
                'ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=size=64x64:rate=5:duration=2',
                '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2', '-shortest',
                '-c:v', 'libx264', '-c:a', 'aac', str(folder / 'source.mp4'),
            ], check=True, capture_output=True)
            (folder / 'source.info.json').write_text(json.dumps({
                'id': 'G4vcmeBFDm0', 'title': 'synthetic fixture, not a real download',
                'channel_id': 'UCR7kylWglbT5pF1UXBybHCw', 'live_status': 'was_live', 'duration': 2,
            }), encoding='utf-8')
            (folder / 'transcript.json').write_text(json.dumps({
                'source_video_id': 'G4vcmeBFDm0', 'machine_generated': True,
                'source': 'synthetic_fixture', 'segments': [{'start': 0, 'end': 1, 'text': 'fixture'}],
            }), encoding='utf-8')
            result = subprocess.run([
                sys.executable, str(Path(__file__).parent / 'analysis.py'), '--root', str(root),
                'sample', '--url', 'https://www.youtube.com/watch?v=G4vcmeBFDm0',
            ], capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['transcript_status'], 'machine_generated_requires_review')
            self.assertEqual(len(json.loads((folder / 'frames.json').read_text(encoding='utf-8'))), 5)

    def test_unreviewed_or_uncovered_content_cannot_be_discarded(self):
        retained = [{'start': 0, 'end': 100, 'category': 'uncertain', 'reviewed': False}]
        self.assertEqual(validate_segments(100, retained), [])
        self.assertTrue(validate_segments(100, [
            {'start': 0, 'end': 20, 'category': 'stock', 'reviewed': True},
            {'start': 25, 'end': 100, 'category': 'uncertain', 'reviewed': False},
        ]))
        self.assertTrue(validate_segments(100, [
            {'start': 0, 'end': 100, 'category': 'non_stock', 'reviewed': False},
        ]))

    def test_sample_probe_rejects_video_without_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'silent.mp4'
            subprocess.run([
                'ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                'color=size=64x64:rate=5:duration=2', '-c:v', 'libx264', str(path),
            ], check=True, capture_output=True)
            with self.assertRaisesRegex(ValueError, 'audio'):
                probe_media(path, 2)

    def test_discovery_does_not_select_running_upcoming_or_unconfirmed_videos(self):
        entries = [
            {'id': 'running', 'live_status': 'is_live'},
            {'id': 'scheduled', 'live_status': 'is_upcoming'},
            {'id': 'processing', 'live_status': 'post_live'},
            {'id': 'unknown'},
            {'id': 'latest', 'live_status': 'was_live', 'duration': 2443},
            {'id': 'older', 'live_status': 'was_live', 'duration': 1934},
        ]
        self.assertEqual([e['id'] for e in select_completed_lives(entries, 1)], ['latest'])

    def test_evidence_requires_original_time_and_existing_frame(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'frame.jpg').write_bytes(b'fixture')
            records = [{
                'claim': '僅測試結構，不是真實分析',
                'source_url': 'https://www.youtube.com/watch?v=G4vcmeBFDm0&t=62s',
                'timestamp_seconds': 62,
                'screenshot': 'frame.jpg',
                'asset': '合成測試', 'timeframe': '未知',
                'indicators': [{'name': '未知', 'parameters': '未知'}],
                'speaker_statement': '未知', 'visible_facts': '合成測試檔',
                'inference': '無', 'drawing_basis': '未知',
                '成立條件': '未知', '失效條件': '未知', '後續修正': '未知',
            }]
            self.assertEqual(validate_records(root, 'G4vcmeBFDm0', 100, records), [])
            records[0]['timestamp_seconds'] = 101
            self.assertTrue(validate_records(root, 'G4vcmeBFDm0', 100, records))


if __name__ == '__main__':
    unittest.main()
