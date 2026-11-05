"""Local camera access for explicitly enabled login snapshots."""
from __future__ import annotations


def capture_jpeg() -> bytes:
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError('Install opencv-python to use webcam captures.') from exc
    import sys
    backend = cv2.CAP_DSHOW if sys.platform == 'win32' else cv2.CAP_ANY
    cam = cv2.VideoCapture(0, backend)
    if not cam.isOpened():
        cam.release()
        raise RuntimeError('Camera unavailable or access was denied by Windows.')
    try:
        cam.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cam.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        frame = None
        for _ in range(8):
            ok, temp = cam.read()
            if ok:
                frame = temp
        if frame is None:
            raise RuntimeError('Could not take a photo from the webcam.')
        ok, encoded = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 87])
        if not ok:
            raise RuntimeError('Camera photo encoding failed.')
        return encoded.tobytes()
    finally:
        cam.release()
