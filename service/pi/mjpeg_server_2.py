#!/usr/bin/python3

# This is the same as mjpeg_server.py, but uses the h/w MJPEG encoder.
# Campi additions: /snapshot.jpg serves one high-quality software-encoded JPEG of the current frame
# (the hardware MJPEG encoder is capped at 25 Mbps, which is soft at 1920x1440/30fps).

import io
import logging
import socketserver
import time
from http import server
from threading import Condition, Lock
from urllib.parse import parse_qs, urlparse

import simplejpeg
from libcamera import controls
from picamera2 import Picamera2
from picamera2.encoders import MJPEGEncoder, Quality
from picamera2.outputs import FileOutput

SNAPSHOT_QUALITY = 90
# Night: once gain reaches 4, auto-exposure stretches a frame up to this long before raising the gain further (the
# stream's fps drops to match; daylight stays at 30 fps). At the stock 1/30 s cap night frames were black (mean luma
# ~1); at 1 s the gain sat at 16 and only the brightest stars showed. 8 s collects 8x the starlight; the timelapse
# then gets a frame every 8 s at night (stars move about 2 arcminutes in that time, under a pixel at 80 deg wide).
NIGHT_MAX_EXPOSURE_US = 8_000_000
NIGHT_STRETCH_GAIN = 4.0  # gain held while the exposure stretches (8 before: AE stopped at 2 s x gain 8)
# Above this exposure the ISP's spatial noise reduction drops to Minimal: it averages neighbouring pixels, which wipes
# out faint stars that are a pixel or two wide (the PC stacks frames over time instead). Daylight and dusk keep Fast,
# and sightings (paused below 4 fps) never see the difference.
NIGHT_NR_EXPOSURE_US = 250_000
SENSOR = "imx477"  # HQ camera; named here because asking libcamera starts it with the stock tuning first
# White balance is locked to daylight so timelapses don't shift color as auto WB chases clouds, dusk and streetlights
# (night scenes come out warm, as they look). Gains come from the sensor's calibrated ct_curve at this temperature.
WB_KELVIN = 5600

PAGE = """\
<html>
<head>
<title>picamera2 MJPEG streaming demo</title>
</head>
<body>
<h1>Picamera2 MJPEG Streaming Demo</h1>
<img src="stream.mjpg" style="width:100%;height:auto" />
</body>
</html>
"""


class StreamingOutput(io.BufferedIOBase):
    def __init__(self):
        self.frame = None
        self.condition = Condition()

    def write(self, buf):
        with self.condition:
            self.frame = buf
            self.condition.notify_all()


snapshot_lock = Lock()
nr_mode = None  # the NoiseReductionMode last set (None until the first snapshot)


def snapshot(quality):
    """Grab the next frame from the running camera and JPEG-encode it in software."""
    global nr_mode
    with snapshot_lock:
        request = picam2.capture_request()
        try:
            arr = request.make_array("main")
            meta = request.get_metadata()
        finally:
            request.release()
        # the timelapse polls this every frame, so noise reduction follows the light within a frame or two
        nr = (controls.draft.NoiseReductionModeEnum.Minimal if meta.get('ExposureTime', 0) > NIGHT_NR_EXPOSURE_US
              else controls.draft.NoiseReductionModeEnum.Fast)
        if nr != nr_mode:
            picam2.set_controls({"NoiseReductionMode": nr})
            nr_mode = nr
    # XBGR8888 arrives as [R, G, B, 255] per pixel.
    jpeg = simplejpeg.encode_jpeg(arr, quality=quality, colorspace="RGBX", colorsubsampling="420")
    r, b = meta.get('ColourGains', (0, 0))
    return jpeg, (f"{meta.get('ExposureTime', 0)} us, gain {meta.get('AnalogueGain', 0):.2f}, wb {r:.2f}/{b:.2f}, "
                  f"nr {nr_mode.name if nr_mode is not None else '-'}")


class StreamingHandler(server.BaseHTTPRequestHandler):
    def do_GET(self):
        url = urlparse(self.path)
        if self.path == '/':
            self.send_response(301)
            self.send_header('Location', '/index.html')
            self.end_headers()
        elif self.path == '/index.html':
            content = PAGE.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', len(content))
            self.end_headers()
            self.wfile.write(content)
        elif url.path == '/snapshot.jpg':
            try:
                q = int(parse_qs(url.query).get('q', [SNAPSHOT_QUALITY])[0])
                t0 = time.monotonic()
                jpeg, exposure = snapshot(max(10, min(q, 100)))
                ms = (time.monotonic() - t0) * 1000
            except Exception as e:
                logging.exception('snapshot failed')
                self.send_error(500, str(e))
                return
            self.send_response(200)
            self.send_header('Content-Type', 'image/jpeg')
            self.send_header('Content-Length', len(jpeg))
            self.send_header('Cache-Control', 'no-cache, private')
            self.send_header('X-Capture-Ms', f'{ms:.0f}')
            self.send_header('X-Exposure', exposure)
            self.end_headers()
            self.wfile.write(jpeg)
        elif self.path == '/stream.mjpg':
            self.send_response(200)
            self.send_header('Age', 0)
            self.send_header('Cache-Control', 'no-cache, private')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=FRAME')
            self.end_headers()
            try:
                while True:
                    with output.condition:
                        output.condition.wait()
                        frame = output.frame
                    self.wfile.write(b'--FRAME\r\n')
                    self.send_header('Content-Type', 'image/jpeg')
                    self.send_header('Content-Length', len(frame))
                    self.end_headers()
                    self.wfile.write(frame)
                    self.wfile.write(b'\r\n')
            except Exception as e:
                logging.warning('Removed streaming client %s: %s', self.client_address, str(e))
        else:
            self.send_error(404)
            self.end_headers()

    def log_request(self, code='-', size='-'):
        if self.path.startswith('/snapshot.jpg') and code == 200:
            return  # one every 2 s would flood the journal
        super().log_request(code, size)


class StreamingServer(socketserver.ThreadingMixIn, server.HTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def night_tuning():
    """The sensor's stock tuning with the normal exposure mode extended to NIGHT_MAX_EXPOSURE_US, and strong
    defective-pixel correction: at night gain hot pixels otherwise survive and demosaic into ~10 px coloured blobs."""
    tuning = Picamera2.load_tuning_file(SENSOR + ".json")
    Picamera2.find_tuning_algo(tuning, "rpi.dpc")["strength"] = 2  # 0 off, 1 normal (stock), 2 strong
    agc = Picamera2.find_tuning_algo(tuning, "rpi.agc")
    for ch in agc.get("channels", [agc]):
        ch["exposure_modes"]["normal"] = {
            "shutter": [100, 10000, 30000, 33333, NIGHT_MAX_EXPOSURE_US, NIGHT_MAX_EXPOSURE_US],
            "gain": [1.0, 2.0, 4.0, NIGHT_STRETCH_GAIN, NIGHT_STRETCH_GAIN, 16.0]}
    return tuning


def wb_gains(tuning, kelvin):
    """(red, blue) colour gains for a grey under `kelvin` light, interpolated from the tuning's ct_curve."""
    c = Picamera2.find_tuning_algo(tuning, "rpi.awb")["ct_curve"]
    pts = [c[i:i + 3] for i in range(0, len(c), 3)]  # [colour temperature, r/g, b/g]
    kelvin = min(max(kelvin, pts[0][0]), pts[-1][0])
    for (k0, r0, b0), (k1, r1, b1) in zip(pts, pts[1:]):
        if k0 <= kelvin <= k1:
            f = (kelvin - k0) / (k1 - k0) if k1 > k0 else 0.0
            return 1 / (r0 + f * (r1 - r0)), 1 / (b0 + f * (b1 - b0))


tuning = night_tuning()
WB_GAINS = wb_gains(tuning, WB_KELVIN)
picam2 = Picamera2(tuning=tuning)
picam2.configure(picam2.create_video_configuration(main={"size": (1920, 1440)}, sensor={"output_size": (2028, 1520)},
                                                   controls={"FrameDurationLimits": (33333, NIGHT_MAX_EXPOSURE_US),
                                                             "AwbEnable": False, "ColourGains": WB_GAINS}))
output = StreamingOutput()
picam2.start_recording(MJPEGEncoder(), FileOutput(output), quality=Quality.HIGH)

try:
    address = ('', 8000)
    server = StreamingServer(address, StreamingHandler)
    server.serve_forever()
finally:
    picam2.stop_recording()
