"""
PipeWire/xdg-desktop-portal screen capture for Wayland compositors.

Uses the org.freedesktop.portal.ScreenCast D-Bus API to capture screen
content on GNOME, KDE, and other Wayland compositors where traditional
X11 capture methods don't work.

Flow:
  1. CreateSession() — create a portal session
  2. SelectSources() — request screen capture (triggers user consent dialog)
  3. Start() — begin streaming via PipeWire
  4. GStreamer pipeline reads PipeWire node → extracts frames

Dependencies (optional, graceful degradation):
  - dbus-python (or dbus-next)
  - PyGObject with GStreamer bindings (gi.repository: Gst, GstApp, GLib)

When deps are missing, PIPEWIRE_AVAILABLE=False and the module is a no-op.
The screencast timer in qt_app_mvc.py falls back to grab_screen_region().
"""

import logging
import threading

logger = logging.getLogger(__name__)

# Try importing portal/GStreamer dependencies
PIPEWIRE_AVAILABLE = False
_IMPORT_ERROR = ""

try:
    import dbus
    import gi
    from dbus.mainloop.glib import DBusGMainLoop
    gi.require_version('Gst', '1.0')
    gi.require_version('GstApp', '1.0')
    from gi.repository import GLib, Gst, GstApp  # noqa: F401
    Gst.init(None)
    PIPEWIRE_AVAILABLE = True
except (ImportError, ValueError) as e:
    _IMPORT_ERROR = str(e)
    logger.info("PipeWire capture not available: %s", e)


# Portal D-Bus constants
_PORTAL_BUS = 'org.freedesktop.portal.Desktop'
_PORTAL_PATH = '/org/freedesktop/portal/desktop'
_SCREENCAST_IFACE = 'org.freedesktop.portal.ScreenCast'
_REQUEST_IFACE = 'org.freedesktop.portal.Request'


class PipeWireScreenCast:
    """Portal-based screen capture using PipeWire + GStreamer.

    Usage:
        cast = PipeWireScreenCast()
        if cast.start():
            # Session started, portal dialog shown to user
            frame = cast.grab_frame()  # Returns (width, height, bytes) or None
            ...
            cast.stop()

    Thread safety:
        - GLib main loop runs in a background thread for D-Bus signals
        - grab_frame() is thread-safe (uses a lock)
        - start()/stop() should be called from the main thread
    """

    def __init__(self):
        self._session_path = None
        self._pipewire_fd = None
        self._node_id = None
        self._pipeline = None
        self._appsink = None
        self._glib_loop = None
        self._glib_thread = None
        self._frame_lock = threading.Lock()
        self._latest_frame = None  # (width, height, bytes_rgb)
        self._running = False
        self._session_ready = threading.Event()
        self._session_failed = threading.Event()

    @property
    def available(self) -> bool:
        """Check if PipeWire capture dependencies are available."""
        return PIPEWIRE_AVAILABLE

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self, timeout: float = 30.0) -> bool:
        """Start a portal screen capture session.

        This will trigger a user consent dialog managed by the compositor.
        The user must approve screen sharing before capture begins.

        Args:
            timeout: Seconds to wait for user to approve the portal dialog.

        Returns:
            True if capture session started successfully.
        """
        if not PIPEWIRE_AVAILABLE:
            logger.warning("PipeWire not available: %s", _IMPORT_ERROR)
            return False

        if self._running:
            return True

        self._session_ready.clear()
        self._session_failed.clear()

        try:
            self._start_glib_loop()
            self._create_session()
        except Exception as e:
            logger.error("Failed to create portal session: %s", e)
            self._cleanup()
            return False

        # Wait for portal dialog approval or failure
        if self._session_ready.wait(timeout):
            self._running = True
            return True

        if self._session_failed.is_set():
            logger.error("Portal session was denied or failed")
        else:
            logger.error("Portal session timed out (user didn't respond)")

        self._cleanup()
        return False

    def stop(self):
        """Stop capture and clean up all resources."""
        self._running = False
        self._cleanup()

    def grab_frame(self):
        """Get the latest captured frame.

        Returns:
            Tuple of (width, height, rgb_bytes) or None if no frame available.
            rgb_bytes is raw RGB pixel data (3 bytes per pixel).
        """
        with self._frame_lock:
            return self._latest_frame

    # --- Internal: D-Bus portal flow ---

    def _start_glib_loop(self):
        """Start GLib main loop in background thread for D-Bus signals."""
        DBusGMainLoop(set_as_default=True)
        self._glib_loop = GLib.MainLoop()
        self._glib_thread = threading.Thread(
            target=self._glib_loop.run, daemon=True)
        self._glib_thread.start()

    def _create_session(self):
        """Step 1: CreateSession on the ScreenCast portal."""
        bus = dbus.SessionBus()
        portal = bus.get_object(_PORTAL_BUS, _PORTAL_PATH)
        screencast = dbus.Interface(portal, _SCREENCAST_IFACE)

        # Unique token for this session
        import random
        token = f"trcc_{random.randint(100000, 999999)}"
        session_token = f"trcc_session_{random.randint(100000, 999999)}"

        request_path = screencast.CreateSession(
            dbus.Dictionary({
                'handle_token': dbus.String(token),
                'session_handle_token': dbus.String(session_token),
            }, signature='sv')
        )

        # Listen for the Response signal
        bus.add_signal_receiver(
            self._on_create_session_response,
            signal_name='Response',
            dbus_interface=_REQUEST_IFACE,
            path=request_path,
        )

    def _on_create_session_response(self, response, results):
        """Handle CreateSession response."""
        if response != 0:
            logger.error("CreateSession failed with response %d", response)
            self._session_failed.set()
            return

        self._session_path = str(results.get('session_handle', ''))
        if not self._session_path:
            logger.error("No session handle in CreateSession response")
            self._session_failed.set()
            return

        logger.info("Portal session created: %s", self._session_path)
        self._select_sources()

    def _select_sources(self):
        """Step 2: SelectSources — request monitor capture."""
        bus = dbus.SessionBus()
        portal = bus.get_object(_PORTAL_BUS, _PORTAL_PATH)
        screencast = dbus.Interface(portal, _SCREENCAST_IFACE)

        import random
        token = f"trcc_src_{random.randint(100000, 999999)}"

        request_path = screencast.SelectSources(
            dbus.ObjectPath(self._session_path),
            dbus.Dictionary({
                'handle_token': dbus.String(token),
                'types': dbus.UInt32(1),       # 1 = MONITOR (not window)
                'multiple': dbus.Boolean(False),
                'persist_mode': dbus.UInt32(2),  # 2 = persist until revoked
            }, signature='sv')
        )

        bus.add_signal_receiver(
            self._on_select_sources_response,
            signal_name='Response',
            dbus_interface=_REQUEST_IFACE,
            path=request_path,
        )

    def _on_select_sources_response(self, response, results):
        """Handle SelectSources response."""
        if response != 0:
            logger.error("SelectSources failed with response %d", response)
            self._session_failed.set()
            return

        logger.info("Sources selected, starting stream...")
        self._start_stream()

    def _start_stream(self):
        """Step 3: Start — begin PipeWire stream (triggers consent dialog)."""
        bus = dbus.SessionBus()
        portal = bus.get_object(_PORTAL_BUS, _PORTAL_PATH)
        screencast = dbus.Interface(portal, _SCREENCAST_IFACE)

        import random
        token = f"trcc_start_{random.randint(100000, 999999)}"

        request_path = screencast.Start(
            dbus.ObjectPath(self._session_path),
            dbus.String(''),  # parent_window (empty = no parent)
            dbus.Dictionary({
                'handle_token': dbus.String(token),
            }, signature='sv')
        )

        bus.add_signal_receiver(
            self._on_start_response,
            signal_name='Response',
            dbus_interface=_REQUEST_IFACE,
            path=request_path,
        )

    def _on_start_response(self, response, results):
        """Handle Start response — get PipeWire node ID and start pipeline."""
        if response != 0:
            logger.error("Start failed with response %d (user denied?)",
                         response)
            self._session_failed.set()
            return

        streams = results.get('streams', [])
        if not streams:
            logger.error("No streams in Start response")
            self._session_failed.set()
            return

        # streams is array of (node_id, properties)
        self._node_id = int(streams[0][0])
        logger.info("PipeWire node ID: %d", self._node_id)

        # Get PipeWire file descriptor
        try:
            bus = dbus.SessionBus()
            portal = bus.get_object(_PORTAL_BUS, _PORTAL_PATH)
            screencast = dbus.Interface(portal, _SCREENCAST_IFACE)
            self._pipewire_fd = screencast.OpenPipeWireRemote(
                dbus.ObjectPath(self._session_path),
                dbus.Dictionary({}, signature='sv'),
            ).take()
        except Exception as e:
            logger.error("OpenPipeWireRemote failed: %s", e)
            self._session_failed.set()
            return

        # Start GStreamer pipeline
        try:
            self._start_gstreamer()
            self._session_ready.set()
        except Exception as e:
            logger.error("GStreamer pipeline failed: %s", e)
            self._session_failed.set()

    # --- Internal: GStreamer pipeline ---

    def _start_gstreamer(self):
        """Create and start GStreamer pipeline to read PipeWire frames."""
        # Pipeline: pipewiresrc → videoconvert → RGB → appsink
        pipeline_str = (
            f"pipewiresrc fd={self._pipewire_fd} path={self._node_id} "
            f"do-timestamp=true keepalive-time=1000 ! "
            f"videoconvert ! "
            f"video/x-raw,format=RGB ! "
            f"appsink name=sink emit-signals=true max-buffers=2 drop=true"
        )

        self._pipeline = Gst.parse_launch(pipeline_str)
        self._appsink = self._pipeline.get_by_name('sink')
        self._appsink.connect('new-sample', self._on_new_sample)

        ret = self._pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("Failed to start GStreamer pipeline")

        logger.info("GStreamer pipeline started")

    def _on_new_sample(self, sink):
        """GStreamer callback: new frame available from PipeWire."""
        sample = sink.emit('pull-sample')
        if sample is None:
            return Gst.FlowReturn.OK

        buf = sample.get_buffer()
        caps = sample.get_caps()

        struct = caps.get_structure(0)
        width = struct.get_int('width')[1]
        height = struct.get_int('height')[1]

        success, map_info = buf.map(Gst.MapFlags.READ)
        if not success:
            return Gst.FlowReturn.OK

        try:
            # Copy frame data (RGB, 3 bytes per pixel)
            rgb_bytes = bytes(map_info.data)
            with self._frame_lock:
                self._latest_frame = (width, height, rgb_bytes)
        finally:
            buf.unmap(map_info)

        return Gst.FlowReturn.OK

    # --- Internal: Cleanup ---

    def _cleanup(self):
        """Stop pipeline, close FD, quit GLib loop."""
        if self._pipeline:
            try:
                self._pipeline.set_state(Gst.State.NULL)
            except Exception:
                pass
            self._pipeline = None
            self._appsink = None

        if self._pipewire_fd is not None:
            try:
                import os
                os.close(self._pipewire_fd)
            except OSError:
                pass
            self._pipewire_fd = None

        if self._session_path:
            try:
                bus = dbus.SessionBus()
                session = bus.get_object(_PORTAL_BUS, self._session_path)
                session_iface = dbus.Interface(
                    session, 'org.freedesktop.portal.Session')
                session_iface.Close()
            except Exception:
                pass
            self._session_path = None

        if self._glib_loop and self._glib_loop.is_running():
            try:
                self._glib_loop.quit()
            except Exception:
                pass
            self._glib_loop = None

        self._node_id = None
        self._latest_frame = None

    def __del__(self):
        self.stop()
