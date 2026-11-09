"""Raw H.264 decoding and bounded automatic recovery, without a live emulator."""
import errno
import io
import subprocess
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

from swordx.preview import (AndroidVideoStream, STREAM_SIZES, STREAM_BITRATES,
                            StreamRetry, screenrecord_command)


class VideoReliabilityTests(unittest.TestCase):
    def test_retry_drops_resolution_and_bitrate_but_never_stops_permanently(self):
        retry = StreamRetry()
        self.assertEqual(retry.profile(), (STREAM_SIZES[0], STREAM_BITRATES[0]))
        for _ in range(15):
            delay = retry.complete(0, 0.2)
            self.assertLessEqual(delay, 8)
        self.assertEqual(retry.profile(), (STREAM_SIZES[-1], STREAM_BITRATES[-1]))
        self.assertTrue(retry.should_log() is False or retry.failures > 0)
        self.assertEqual(retry.complete(90, 3.5), 0.2)
        self.assertEqual(retry.failures, 0)
        self.assertEqual(retry.profile(), (STREAM_SIZES[0], STREAM_BITRATES[0]))

    def test_slow_video_does_not_reset_retry_policy(self):
        retry = StreamRetry()
        retry.complete(1, 2)
        retry.complete(30, 5)  # 6 fps isn't healthy playback.
        self.assertEqual(retry.failures, 2)
        self.assertFalse(retry.should_log() is False and retry.failures == 1)

    def test_landscape_encoder_profile_has_correct_orientation(self):
        retry = StreamRetry()
        self.assertEqual(retry.profile(landscape=True), ((768, 432), STREAM_BITRATES[0]))
        retry.complete(0, .1)
        self.assertEqual(retry.profile(landscape=True), ((512, 288), STREAM_BITRATES[1]))

    def test_low_profile_is_device_scoped(self):
        cmd = screenrecord_command('127.0.0.1:16384', STREAM_SIZES[1],
                                   'adb.exe', STREAM_BITRATES[1])
        self.assertEqual(cmd[1:3], ['-s', '127.0.0.1:16384'])
        self.assertIn(str(STREAM_BITRATES[1]), cmd)
        with self.assertRaises(ValueError):
            screenrecord_command('127.0.0.1:16384', STREAM_SIZES[0], bitrate=-1)

    def test_parser_accepts_short_chunks_and_assembles_packets(self):
        fake_decoder = MagicMock()
        rgb = np.full((4, 4, 3), 23, dtype=np.uint8)
        def parse(data):
            return [] if not data else [data]
        fake_decoder.parse.side_effect = parse
        fake_decoder.decode.side_effect = lambda packet: ([MagicMock(to_ndarray=lambda **_: rgb)]
                                                        if packet is not None else [])
        fake_av = types.SimpleNamespace(CodecContext=types.SimpleNamespace(create=lambda *_: fake_decoder))
        stream = AndroidVideoStream('127.0.0.1:16384')
        stream.process = MagicMock(stdout=io.BytesIO(b'chunk1chunk2'))
        with patch.dict(sys.modules, {'av': fake_av}):
            frames = list(stream.frames())
        self.assertEqual(len(frames), 1)
        self.assertTrue(np.array_equal(frames[0], rgb))
        fake_decoder.parse.assert_any_call(b'')
        fake_decoder.decode.assert_any_call(None)

    def test_interrupted_stream_never_flushes_truncated_packet(self):
        decoder = MagicMock()
        decoder.parse.side_effect = lambda data: []
        fake_av = types.SimpleNamespace(CodecContext=types.SimpleNamespace(create=lambda *_: decoder))
        stream = AndroidVideoStream('127.0.0.1:16384')
        stream.process = MagicMock(stdout=io.BytesIO(b'partial'))
        stream._interrupted = True
        with patch.dict(sys.modules, {'av': fake_av}):
            self.assertEqual(list(stream.frames()), [])
        decoder.parse.assert_called_once_with(b'partial')
        decoder.decode.assert_not_called()

    def test_error_22_preserves_explanation_and_adb_context(self):
        err = OSError(errno.EINVAL, 'Invalid argument')
        result = AndroidVideoStream.explain_failure(err, 'MediaCodec encoder unavailable')
        self.assertIn('error 22', result)
        self.assertIn('MediaCodec encoder unavailable', result)

    def test_interrupt_tolerates_already_exited_subprocess(self):
        stream = AndroidVideoStream('127.0.0.1:16384')
        stream.process = MagicMock()
        stream.process.poll.return_value = None
        stream.process.kill.side_effect = ProcessLookupError()
        stream.interrupt()
        self.assertTrue(stream._interrupted)


if __name__ == '__main__':
    unittest.main()

class RealH264FixtureTests(unittest.TestCase):
    def test_annexb_h264_decodes_after_arbitrary_short_reads(self):
        try:
            import av  # noqa: F401
        except ImportError:
            self.skipTest('PyAV unavailable locally; CI installs the actual decoder')
        from pathlib import Path
        payload = (Path(__file__).parent / 'fixtures' / 'h264_annexb.h264').read_bytes()
        # Deliberately split SPS/PPS and access units into short chunks.
        class ShortPipe(io.BytesIO):
            def read(self, length=-1):
                return super().read(min(length, 113))
        stream = AndroidVideoStream('127.0.0.1:16384')
        stream.process = MagicMock(stdout=ShortPipe(payload))
        result = list(stream.frames())
        self.assertGreaterEqual(len(result), 12)
        self.assertEqual(result[-1].shape, (64, 64, 3))


class StaticSurfaceTests(unittest.TestCase):
    def test_static_screen_does_not_trigger_encoder_restart(self):
        from swordx.preview import screen_is_unchanged
        rgb = np.full((480, 270, 3), (120, 60, 15), dtype=np.uint8)
        bgr = rgb[:, :, ::-1].copy()
        self.assertTrue(screen_is_unchanged(rgb, bgr))

    def test_new_game_screen_forces_recovery(self):
        from swordx.preview import screen_is_unchanged
        rgb = np.zeros((480, 270, 3), dtype=np.uint8)
        new = np.zeros_like(rgb)
        new[120:330, 40:235] = 200
        self.assertFalse(screen_is_unchanged(rgb, new))

    def test_letterboxed_idle_surface_does_not_restart_encoder(self):
        from swordx.preview import fit_preview, screen_is_unchanged
        import cv2
        screenshot = np.full((600, 800, 3), (14, 70, 200), dtype=np.uint8)
        cv2.rectangle(screenshot, (160, 100), (550, 370), (230, 40, 80), -1)
        video_rgb = fit_preview(cv2.cvtColor(screenshot, cv2.COLOR_BGR2RGB), 480, 270)
        self.assertTrue(screen_is_unchanged(video_rgb, screenshot))

    def test_letterboxed_changed_surface_still_restarts_encoder(self):
        from swordx.preview import fit_preview, screen_is_unchanged
        import cv2
        screenshot = np.full((600, 800, 3), 80, dtype=np.uint8)
        old_video = fit_preview(cv2.cvtColor(screenshot, cv2.COLOR_BGR2RGB), 480, 270)
        screenshot[150:300, 200:600] = 215
        self.assertFalse(screen_is_unchanged(old_video, screenshot))

    def test_small_but_distinct_button_change_forces_recovery(self):
        from swordx.preview import screen_is_unchanged
        rgb = np.zeros((480, 270, 3), dtype=np.uint8)
        sample = np.zeros_like(rgb)
        sample[200:250, 100:150] = 255
        self.assertFalse(screen_is_unchanged(rgb, sample))

class StreamFailureTests(unittest.TestCase):
    def test_invalid_h264_encoder_error_is_not_mistaken_for_success(self):
        fake = MagicMock()
        fake.poll.return_value = 22
        fake.returncode = 22
        fake.stdout = io.BytesIO()
        with patch('swordx.preview.subprocess.Popen', return_value=fake):
            with AndroidVideoStream('127.0.0.1:16384') as stream:
                stream.stderr_file.write(b'MediaCodec error: EINVAL (22)')
                self.assertIn('MediaCodec error', stream.error())

    def test_two_termination_timeouts_preserve_error_and_release_pipes(self):
        stream = AndroidVideoStream('127.0.0.1:16384')
        proc = MagicMock()
        proc.poll.return_value = None
        proc.stdout = io.BytesIO(b'incomplete h264')
        proc.wait.side_effect = [subprocess.TimeoutExpired('adb', 2),
                                 subprocess.TimeoutExpired('adb', 2)]
        log = io.BytesIO(b'MediaCodec error 22')
        stream.process, stream.stderr_file = proc, log

        stream.close()
        self.assertTrue(proc.stdout.closed)
        self.assertTrue(log.closed)
        self.assertIsNone(stream.process)
        self.assertIsNone(stream.stderr_file)
        self.assertIn('MediaCodec error 22', stream.error())
        self.assertIn('teardown incomplete', stream.error())
        self.assertEqual(proc.wait.call_count, 2)
        stream.close()  # Cleanup is idempotent after a failed kill/wait.

    def test_process_exits_during_second_kill_and_still_closes_stream(self):
        stream = AndroidVideoStream('127.0.0.1:16384')
        proc = MagicMock()
        proc.poll.return_value = None
        proc.stdout = io.BytesIO(b'')
        proc.kill.side_effect = [None, ProcessLookupError('already gone')]
        proc.wait.side_effect = [subprocess.TimeoutExpired('adb', 2)]
        stream.process, stream.stderr_file = proc, io.BytesIO()

        stream.close()
        self.assertTrue(proc.stdout.closed)
        self.assertIsNone(stream.process)
        self.assertIn('teardown incomplete', stream.error())

    def test_wait_oserror_does_not_skip_stream_cleanup(self):
        stream = AndroidVideoStream('127.0.0.1:16384')
        proc = MagicMock()
        proc.poll.return_value = None
        proc.stdout = io.BytesIO()
        proc.wait.side_effect = OSError('invalid process handle')
        stream.process, stream.stderr_file = proc, io.BytesIO(b'encoder failed')

        stream.close()
        self.assertTrue(proc.stdout.closed)
        self.assertIsNone(stream.process)
        self.assertIn('invalid process handle', stream.error())
        self.assertIn('encoder failed', stream.error())

    def test_normal_stream_exit_and_error_do_not_leak_process(self):
        fake = MagicMock()
        fake.poll.return_value = 0
        fake.returncode = 0
        fake.stdout = io.BytesIO()
        with patch('swordx.preview.subprocess.Popen', return_value=fake):
            stream = AndroidVideoStream('127.0.0.1:16384')
            with stream:
                self.assertIn('exited (0)', stream.error())
            fake.wait.assert_called()
            self.assertIsNone(stream.process)