"""Volatile, in-process video preview for encrypted VEIL ZIP entries.

Video bytes are supplied in RAM by the authenticated ZIP reader. No media file
is created in Windows temporary storage or exported. Decoder buffers, memory
pages and sound subsystems are beyond the vault's at-rest confidentiality model.

PyAV (FFmpeg-backed) provides the codec; sounddevice provides optional audio.
The preview intentionally has no external URL/network or OS media-player hooks.
"""
from __future__ import annotations

import io
import threading
import time
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageTk

from .core import VaultError

VIDEO_EXTENSIONS = frozenset({'.mp4', '.m4v', '.mov', '.webm', '.mkv', '.avi'})
MAX_VIDEO_BYTES = 160 * 1024 * 1024
MAX_PREVIEW_DIMENSION = (960, 540)


def video_available() -> bool:
    try:
        import av  # noqa: F401
        return True
    except ImportError:
        return False


class MemoryVideoPlayer:
    """Read-only Tk playback, with optional audio, using in-memory video data.

    The lock/close path calls .close(), cancels UI callbacks and signals the
    audio thread to stop. Media memory is freed when windows/references close.
    """

    def __init__(self, owner: tk.Misc, raw: bytes, title: str, on_close=None):
        if len(raw) > MAX_VIDEO_BYTES:
            raise VaultError('Video exceeds the 160 MiB in-app playback limit.')
        try:
            import av
        except ImportError as exc:
            raise VaultError('Video playback requires PyAV. Re-run Start-VEIL.bat to install dependencies.') from exc
        self.av = av
        self.raw = raw
        self._on_close = on_close
        self._closed = False
        self._playing = False
        self._finished = False
        self._timer = None
        self._image = None
        self._next = None
        self._container = None
        self._frames = None
        self._first_pts = None
        self._start_wall = 0.0
        self._paused_at = 0.0
        self._stop_audio = threading.Event()
        self._audio_permission = threading.Event()
        self._audio_error = None
        self._audio_thread = None
        self._video_stream = None
        self._has_audio = False
        self._duration = 0.0

        try:
            self._reset_decoder()
        except Exception as exc:
            if self._container is not None:
                self._container.close()
            raise VaultError(f'Unable to decode this video: {exc}') from exc

        self.win = tk.Toplevel(owner)
        self.win.title(f'VEIL | {title} — encrypted ZIP video')
        self.win.geometry('900x690')
        self.win.minsize(560, 450)
        self.win.configure(bg='#10151f')
        self.win.protocol('WM_DELETE_WINDOW', self.close)
        self.win.bind('<Destroy>', self._on_destroy, add='+')

        header = tk.Frame(self.win, bg='#10151f')
        header.pack(fill='x', padx=15, pady=(12, 10))
        tk.Label(header, text=title, font=('Segoe UI', 15, 'bold'),
                 bg='#10151f', fg='#f2f5fc').pack(anchor='w')
        tk.Label(header, text='In-memory, read-only preview • No video exported to disk',
                 font=('Segoe UI', 9), bg='#10151f', fg='#a8b5c7').pack(anchor='w', pady=(3, 0))
        stage = tk.Frame(self.win, bg='black')
        stage.pack(fill='both', expand=True, padx=15)
        self.video_label = tk.Label(stage, text='Loading video…', bg='black', fg='#f2f5fc')
        self.video_label.pack(fill='both', expand=True)
        controls = tk.Frame(self.win, bg='#10151f')
        controls.pack(fill='x', padx=15, pady=(10, 5))
        self.play_button = ttk.Button(controls, text='Pause', command=self.toggle_play)
        self.play_button.pack(side='left', padx=(0, 8))
        ttk.Button(controls, text='Replay', command=self.replay).pack(side='left', padx=(0, 8))
        ttk.Button(controls, text='Close', command=self.close).pack(side='right')
        self.clock_label = tk.Label(controls, text='0:00', bg='#10151f', fg='#f2f5fc')
        self.clock_label.pack(side='left', padx=10)
        self.status_label = tk.Label(self.win, bg='#10151f', fg='#a8b5c7',
                                     font=('Segoe UI', 9), anchor='w')
        self.status_label.pack(fill='x', padx=15, pady=(0, 12))
        self.status_label.configure(text='Audio enabled when a compatible audio device is available.'
                                    if self._has_audio else 'Video contains no audio track.')
        self.play()

    def _reset_decoder(self):
        if self._container is not None:
            self._container.close()
            self._container = None
        container = self.av.open(io.BytesIO(self.raw), mode='r')
        video_streams = list(container.streams.video)
        if not video_streams:
            container.close()
            raise VaultError('No supported video track found in this file.')
        self._container = container
        self._video_stream = video_streams[0]
        self._frames = iter(container.decode(video=0))
        self._next = None
        self._first_pts = None
        self._has_audio = bool(container.streams.audio)
        if self._video_stream.duration and self._video_stream.time_base:
            self._duration = float(self._video_stream.duration * self._video_stream.time_base)
        elif container.duration:
            self._duration = float(container.duration / self.av.time_base)

    def _on_destroy(self, event):
        if event.widget is self.win:
            self.close()

    def play(self):
        if self._closed or self._playing:
            return
        if self._paused_at:
            self._start_wall += time.monotonic() - self._paused_at
            self._paused_at = 0
        else:
            self._start_wall = time.monotonic()
        self._playing = True
        self._audio_permission.set()
        self.play_button.configure(text='Pause')
        if self._has_audio and self._audio_thread is None:
            self._audio_thread = threading.Thread(target=self._play_audio, daemon=True)
            self._audio_thread.start()
        self._schedule(1)

    def pause(self):
        if self._closed or not self._playing:
            return
        self._playing = False
        self._paused_at = time.monotonic()
        self._audio_permission.clear()
        self.play_button.configure(text='Play')
        self._cancel_timer()

    def toggle_play(self):
        if self._playing:
            self.pause()
        elif self._finished:
            self.replay()
        else:
            self.play()

    def replay(self):
        if self._closed:
            return
        self._cancel_timer()
        self._playing = False
        self._stop_audio.set()
        self._audio_permission.set()
        # Old decoder/audio worker may need a moment to exit; each has a private
        # BytesIO handle and stop event, so restarting never reuses it.
        self._audio_thread = None
        self._finished = False
        self._stop_audio = threading.Event()
        self._audio_permission = threading.Event()
        try:
            self._reset_decoder()
        except Exception as exc:
            self.status_label.configure(text=f'Playback error: {exc}')
            self.play_button.configure(text='Play')
            return
        self._paused_at = 0.0
        self._audio_error = None
        self.clock_label.configure(text='0:00')
        self.play()

    def _schedule(self, delay_ms: int):
        if self._closed:
            return
        self._cancel_timer()
        self._timer = self.win.after(max(8, delay_ms), self._tick)

    def _cancel_timer(self):
        if self._timer is not None:
            try:
                self.win.after_cancel(self._timer)
            except (tk.TclError, RuntimeError):
                pass
            self._timer = None

    def _tick(self):
        self._timer = None
        if self._closed or not self._playing:
            return
        if self._audio_error:
            self.status_label.configure(text=f'Audio unavailable; playing video without sound: {self._audio_error}')
            self._audio_error = None
        try:
            if self._next is None:
                self._next = next(self._frames)
            frame = self._next
            timestamp = frame.time
            if timestamp is None:
                timestamp = float(frame.pts * frame.time_base) if frame.pts is not None else None
            if timestamp is None:
                timestamp = self._first_pts if self._first_pts is not None else 0.0
            if self._first_pts is None:
                self._first_pts = float(timestamp)
            seconds = max(0.0, float(timestamp) - self._first_pts)
            delay = seconds - (time.monotonic() - self._start_wall)
            if delay > 0.025:
                self._schedule(min(80, int(delay * 1000)))
                return
            self._next = None
            # Skip very late frames rather than permanently accumulating lag.
            if delay >= -0.20:
                if frame.width * frame.height > 4096 * 2160:
                    raise VaultError('Video resolution exceeds the supported in-app preview limit.')
                image = frame.to_image()
                image.thumbnail(MAX_PREVIEW_DIMENSION, Image.Resampling.LANCZOS)
                self._image = ImageTk.PhotoImage(image, master=self.win)
                self.video_label.configure(image=self._image, text='')
            minute, second = divmod(int(seconds), 60)
            duration = f' / {int(self._duration)//60}:{int(self._duration)%60:02d}' if self._duration else ''
            self.clock_label.configure(text=f'{minute}:{second:02d}{duration}')
            self._schedule(1)
        except StopIteration:
            self._finished = True
            self._playing = False
            self._audio_permission.clear()
            self.play_button.configure(text='Replay')
            self.status_label.configure(text='Finished. Choose Replay to watch again.')
        except Exception as exc:
            self._finished = True
            self._playing = False
            self._audio_permission.clear()
            self.play_button.configure(text='Replay')
            self.status_label.configure(text=f'Video decoding error: {exc}')

    def _play_audio(self):
        stop = self._stop_audio
        permission = self._audio_permission
        media_data = self.raw
        try:
            import numpy as np
            import sounddevice as sd
            av = self.av
            audio_source = av.open(io.BytesIO(media_data), mode='r')
            try:
                resampler = av.AudioResampler(format='fltp', layout='stereo', rate=48000)
                with sd.OutputStream(samplerate=48000, channels=2, dtype='float32') as out:
                    for frame in audio_source.decode(audio=0):
                        if stop.is_set():
                            break
                        for sample_frame in resampler.resample(frame):
                            if stop.is_set():
                                break
                            data = np.ascontiguousarray(sample_frame.to_ndarray().T, dtype=np.float32)
                            # Chunking lets lock/pause take effect in bounded time.
                            for begin in range(0, len(data), 2048):
                                while not stop.is_set() and not permission.wait(0.05):
                                    pass
                                if stop.is_set():
                                    break
                                out.write(data[begin:begin + 2048])
            finally:
                audio_source.close()
        except Exception as exc:
            if not stop.is_set():
                self._audio_error = str(exc)

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._cancel_timer()
        self._stop_audio.set()
        self._audio_permission.set()
        if self._container is not None:
            self._container.close()
            self._container = None
        self._frames = None
        self._next = None
        self._image = None
        self.raw = b''
        try:
            if self.win.winfo_exists():
                self.win.destroy()
        except (tk.TclError, RuntimeError):
            pass
        if self._on_close:
            self._on_close(self)
