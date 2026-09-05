#!/usr/bin/env python3
"""Transparent, click-through 18x32 arena-grid calibration overlay for macOS."""

from __future__ import annotations

import argparse
import json
import queue
import sys
import threading
import time
from pathlib import Path

from AppKit import (
    NSApp,
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSBackingStoreBuffered,
    NSBezierPath,
    NSButton,
    NSColor,
    NSEvent,
    NSFloatingWindowLevel,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSMakePoint,
    NSMakeRect,
    NSPanel,
    NSScreen,
    NSStatusWindowLevel,
    NSTextField,
    NSTimer,
    NSView,
    NSWindow,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorStationary,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskTitled,
    NSWindowStyleMaskUtilityWindow,
)
from Foundation import NSObject, NSString
from objc import python_method, super as objc_super

import frida


COLUMNS = 18
ROWS = 32
MIN_WIDTH = 180.0
MIN_HEIGHT = 320.0
RUNNING_APP = None


class GridOverlayView(NSView):
    def initWithManager_(self, manager):
        self = objc_super(GridOverlayView, self).initWithFrame_(NSMakeRect(0, 0, 1, 1))
        if self is None:
            return None
        self.manager = manager
        self.drag_mode = None
        self.start_frame = None
        self.start_mouse = None
        return self

    def acceptsFirstMouse_(self, _event):
        return True

    def drawRect_(self, _dirty_rect):
        bounds = self.bounds()
        width = bounds.size.width
        height = bounds.size.height
        if width <= 0 or height <= 0:
            return

        color = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.15, 0.95, 0.70, 0.58)
        color.setStroke()
        border = NSBezierPath.bezierPathWithRect_(bounds)
        border.setLineWidth_(2.0 if self.manager.calibration_mode else 1.0)
        border.stroke()

        grid = NSBezierPath.bezierPath()
        grid.setLineWidth_(0.7)
        for column in range(1, COLUMNS):
            x = width * column / COLUMNS
            grid.moveToPoint_(NSMakePoint(x, 0))
            grid.lineToPoint_(NSMakePoint(x, height))
        for row in range(1, ROWS):
            y = height * row / ROWS
            grid.moveToPoint_(NSMakePoint(0, y))
            grid.lineToPoint_(NSMakePoint(width, y))
        grid.stroke()

        now = time.monotonic()
        for marker in self.manager.markers:
            if marker["expires_at"] <= now:
                continue
            x = marker["column"] * width / COLUMNS
            y = (ROWS - marker["row"] - 1) * height / ROWS
            NSColor.colorWithCalibratedRed_green_blue_alpha_(1.0, 0.12, 0.10, 0.48).setFill()
            NSBezierPath.bezierPathWithRect_(NSMakeRect(x, y, width / COLUMNS, height / ROWS)).fill()
            NSColor.colorWithCalibratedRed_green_blue_alpha_(1.0, 0.22, 0.16, 0.95).setStroke()
            marker_path = NSBezierPath.bezierPathWithRect_(NSMakeRect(x + 1, y + 1, width / COLUMNS - 2, height / ROWS - 2))
            marker_path.setLineWidth_(2.0)
            marker_path.stroke()

            label = NSString.stringWithString_(marker["card_name"])
            attributes = {
                NSFontAttributeName: NSFont.systemFontOfSize_(10),
                NSForegroundColorAttributeName: NSColor.whiteColor(),
            }
            label_size = label.sizeWithAttributes_(attributes)
            banner_width = label_size.width + 10
            banner_height = label_size.height + 4
            banner_x = min(max(2, x + width / COLUMNS / 2 - banner_width / 2), width - banner_width - 2)
            banner_y = y + height / ROWS if y + height / ROWS + banner_height <= height else y - banner_height
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.60, 0.04, 0.03, 0.90).setFill()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(banner_x, banner_y, banner_width, banner_height), 3, 3
            ).fill()
            label.drawAtPoint_withAttributes_(NSMakePoint(banner_x + 5, banner_y + 2), attributes)

        if self.manager.calibration_mode:
            accent = NSColor.colorWithCalibratedRed_green_blue_alpha_(1.0, 0.70, 0.15, 0.85)
            accent.setFill()
            for x, y in ((0, 0), (width - 9, 0), (0, height - 9), (width - 9, height - 9)):
                NSBezierPath.bezierPathWithRect_(NSMakeRect(x, y, 9, 9)).fill()

    def mouseDown_(self, event):
        point = self.convertPoint_fromView_(event.locationInWindow(), None)
        bounds = self.bounds()
        edge = 12.0
        horizontal = "left" if point.x < edge else "right" if point.x > bounds.size.width - edge else None
        vertical = "bottom" if point.y < edge else "top" if point.y > bounds.size.height - edge else None
        self.drag_mode = (horizontal, vertical) if horizontal or vertical else ("move", None)
        self.start_frame = self.window().frame()
        self.start_mouse = self.window().convertPointToScreen_(event.locationInWindow())

    def mouseDragged_(self, event):
        if self.start_frame is None or self.start_mouse is None:
            return
        current = self.window().convertPointToScreen_(event.locationInWindow())
        dx = current.x - self.start_mouse.x
        dy = current.y - self.start_mouse.y
        frame = self.start_frame
        x, y = frame.origin.x, frame.origin.y
        width, height = frame.size.width, frame.size.height
        horizontal, vertical = self.drag_mode

        if horizontal == "move":
            x += dx
            y += dy
        elif horizontal == "left":
            width = max(MIN_WIDTH, width - dx)
            x = frame.origin.x + frame.size.width - width
        elif horizontal == "right":
            width = max(MIN_WIDTH, width + dx)

        if vertical == "bottom":
            height = max(MIN_HEIGHT, height - dy)
            y = frame.origin.y + frame.size.height - height
        elif vertical == "top":
            height = max(MIN_HEIGHT, height + dy)

        self.window().setFrame_display_(NSMakeRect(x, y, width, height), True)
        self.manager.update_status()

    def mouseUp_(self, _event):
        self.drag_mode = None
        self.start_frame = None
        self.start_mouse = None


class OverlayPanel(NSPanel):
    def initWithManager_frame_(self, manager, frame):
        self = objc_super(OverlayPanel, self).initWithContentRect_styleMask_backing_defer_(
            frame,
            NSWindowStyleMaskBorderless,
            NSBackingStoreBuffered,
            False,
        )
        if self is None:
            return None
        self.setOpaque_(False)
        self.setBackgroundColor_(NSColor.clearColor())
        self.setHasShadow_(False)
        self.setLevel_(NSStatusWindowLevel)
        self.setHidesOnDeactivate_(False)
        self.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorFullScreenAuxiliary
            | NSWindowCollectionBehaviorStationary
        )
        self.setReleasedWhenClosed_(False)
        self.setContentView_(GridOverlayView.alloc().initWithManager_(manager))
        return self


class PaletteController(NSObject):
    def initWithManager_(self, manager):
        self = objc_super(PaletteController, self).init()
        if self is None:
            return None
        self.manager = manager
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(24, 80, 300, 262),
            NSWindowStyleMaskTitled | NSWindowStyleMaskUtilityWindow,
            NSBackingStoreBuffered,
            False,
        )
        self.window.setTitle_("Arena Grid Calibration")
        self.window.setLevel_(NSStatusWindowLevel)
        self.window.setReleasedWhenClosed_(False)
        self._build_controls()
        return self

    @python_method
    def _label(self, text, frame, size=12):
        label = NSTextField.labelWithString_(text)
        label.setFrame_(frame)
        label.setFont_(NSFont.systemFontOfSize_(size))
        self.window.contentView().addSubview_(label)
        return label

    @python_method
    def _button(self, title, frame, action):
        button = NSButton.alloc().initWithFrame_(frame)
        button.setTitle_(title)
        button.setTarget_(self)
        button.setAction_(action)
        self.window.contentView().addSubview_(button)
        return button

    @python_method
    def _build_controls(self):
        self.mode_label = self._label("", NSMakeRect(18, 214, 264, 22), 13)
        self.frame_label = self._label("", NSMakeRect(18, 178, 264, 30), 11)
        self._label("Drag inside the grid to move. Drag its edge or corner to resize.", NSMakeRect(18, 152, 264, 20), 11)
        self.mode_button = self._button("", NSMakeRect(18, 111, 128, 28), "toggleCalibration:")
        self._button("Save Profile", NSMakeRect(154, 111, 128, 28), "saveProfile:")
        self._button("Load Profile", NSMakeRect(18, 75, 128, 28), "loadProfile:")
        self._button("Reset Grid", NSMakeRect(154, 75, 128, 28), "resetGrid:")
        self.flip_button = self._button("", NSMakeRect(18, 39, 128, 28), "toggleFlip:")
        self._button("Quit", NSMakeRect(204, 8, 78, 24), "quit:")

    @python_method
    def refresh(self):
        mode = "CALIBRATION: grid accepts mouse input" if self.manager.calibration_mode else "LOCKED: grid is click-through"
        self.mode_label.setStringValue_(mode)
        self.mode_button.setTitle_("Lock Overlay" if self.manager.calibration_mode else "Calibrate")
        self.flip_button.setTitle_("Unflip Arena" if self.manager.flip_view else "Flip Arena 180 deg")
        frame = self.manager.overlay.frame()
        top = self.manager.screen.frame().size.height - (frame.origin.y + frame.size.height)
        self.frame_label.setStringValue_(f"x={frame.origin.x:.0f}  y={top:.0f}  width={frame.size.width:.0f}  height={frame.size.height:.0f}")

    def toggleCalibration_(self, _sender):
        self.manager.set_calibration_mode(not self.manager.calibration_mode)

    def saveProfile_(self, _sender):
        self.manager.save_profile()

    def loadProfile_(self, _sender):
        self.manager.load_profile()

    def resetGrid_(self, _sender):
        self.manager.reset_frame()

    def toggleFlip_(self, _sender):
        self.manager.flip_view = not self.manager.flip_view
        self.manager.overlay.contentView().setNeedsDisplay_(True)
        self.manager.update_status()

    def quit_(self, _sender):
        NSApp.terminate_(None)


class OverlayEventPump(NSObject):
    def initWithManager_(self, manager):
        self = objc_super(OverlayEventPump, self).init()
        if self is None:
            return None
        self.manager = manager
        return self

    def tick_(self, _timer):
        self.manager.drain_events()


class QueueMonitor:
    def __init__(self, target: str, script_path: Path, events: queue.SimpleQueue):
        self.target = target
        self.script_path = script_path
        self.events = events
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, name="queue-monitor", daemon=True)

    def start(self):
        self.thread.start()

    def run(self):
        session = None
        try:
            source = self.script_path.read_text(encoding="utf-8")
            device = frida.get_device_manager().add_remote_device("127.0.0.1:27042")
            session = device.attach(int(self.target) if self.target.isdecimal() else self.target)
            script = session.create_script(source)

            def on_message(message, _data):
                if message.get("type") != "send":
                    return
                try:
                    event = json.loads(message["payload"])
                except (TypeError, json.JSONDecodeError):
                    return
                if event.get("event") == "queue_deploy":
                    self.events.put(event)

            script.on("message", on_message)
            script.load()
            print("Queue monitor attached", flush=True)
            self.events.put({"event": "monitor_ready"})
            while not self.stop_event.wait(0.2):
                pass
        except Exception as error:
            print(f"Queue monitor error: {error}", file=sys.stderr, flush=True)
        finally:
            if session is not None:
                session.detach()

    def stop(self):
        self.stop_event.set()

class ArenaGridApp:
    def __init__(self, profile_path: Path, start_locked: bool, monitor_target: str | None):
        self.profile_path = profile_path
        self.screen = NSScreen.mainScreen()
        self.calibration_mode = not start_locked
        self.flip_view = False
        self.card_names = self.load_card_names()
        self.markers = []
        self.events = queue.SimpleQueue()
        self.monitor = None
        self.overlay = OverlayPanel.alloc().initWithManager_frame_(self, self.default_frame())
        self.palette = PaletteController.alloc().initWithManager_(self)
        self.load_profile(silent=True)
        self.set_calibration_mode(self.calibration_mode)
        self.overlay.orderFrontRegardless()
        self.palette.window.makeKeyAndOrderFront_(None)
        self.update_status()
        self.event_pump = OverlayEventPump.alloc().initWithManager_(self)
        self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(0.03, self.event_pump, "tick:", None, True)
        if monitor_target:
            self.monitor = QueueMonitor(monitor_target, Path(__file__).with_name("hook_queue_deploy.js"), self.events)
            self.monitor.start()

    def default_frame(self):
        visible = self.screen.visibleFrame()
        height = min(visible.size.height * 0.82, 900.0)
        width = height * COLUMNS / ROWS
        return NSMakeRect(visible.origin.x + 80, visible.origin.y + 60, width, height)

    @staticmethod
    def load_card_names():
        path = Path(__file__).with_name("card_catalog.json")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return {int(item["id"]): item["name"] for item in data["items"]}
        except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return {}

    def set_calibration_mode(self, enabled: bool):
        self.calibration_mode = enabled
        self.overlay.setIgnoresMouseEvents_(not enabled)
        self.overlay.contentView().setNeedsDisplay_(True)
        self.update_status()

    def update_status(self):
        if hasattr(self, "palette"):
            self.palette.refresh()

    def profile_data(self):
        frame = self.overlay.frame()
        screen_frame = self.screen.frame()
        top = screen_frame.size.height - (frame.origin.y + frame.size.height)
        corners = [
            {"x": frame.origin.x, "y": top},
            {"x": frame.origin.x + frame.size.width, "y": top},
            {"x": frame.origin.x + frame.size.width, "y": top + frame.size.height},
            {"x": frame.origin.x, "y": top + frame.size.height},
        ]
        return {
            "version": 1,
            "grid": {"columns": COLUMNS, "rows": ROWS},
            "flip_view": self.flip_view,
            "screen": {"width": screen_frame.size.width, "height": screen_frame.size.height},
            "frame_top_left": {"x": frame.origin.x, "y": top, "width": frame.size.width, "height": frame.size.height},
            "corners": corners,
        }

    def save_profile(self):
        self.profile_path.parent.mkdir(parents=True, exist_ok=True)
        self.profile_path.write_text(json.dumps(self.profile_data(), indent=2) + "\n", encoding="utf-8")
        print(f"Saved overlay profile: {self.profile_path}", flush=True)

    def load_profile(self, silent=False):
        if not self.profile_path.exists():
            if not silent:
                print(f"No profile found: {self.profile_path}", flush=True)
            return
        try:
            data = json.loads(self.profile_path.read_text(encoding="utf-8"))
            saved = data["frame_top_left"]
            self.flip_view = bool(data.get("flip_view", False))
            screen_height = self.screen.frame().size.height
            frame = NSMakeRect(
                float(saved["x"]),
                screen_height - float(saved["y"]) - float(saved["height"]),
                float(saved["width"]),
                float(saved["height"]),
            )
            self.overlay.setFrame_display_(frame, True)
            self.update_status()
            if not silent:
                print(f"Loaded overlay profile: {self.profile_path}", flush=True)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            print(f"Could not load overlay profile: {error}", file=sys.stderr, flush=True)

    def reset_frame(self):
        self.overlay.setFrame_display_(self.default_frame(), True)
        self.update_status()

    def drain_events(self):
        changed = False
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            if event.get("event") == "monitor_ready":
                print("Queue overlay ready", flush=True)
                continue
            tile = event.get("target_tile_center", {})
            x, y = tile.get("x"), tile.get("y")
            if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
                continue
            column = int(x - 0.5)
            row = int(y - 0.5)
            if self.flip_view:
                column = COLUMNS - 1 - column
                row = ROWS - 1 - row
            if not 0 <= column < COLUMNS or not 0 <= row < ROWS:
                continue
            card_id = event.get("card_id")
            card_name = self.card_names.get(card_id, f"Card {card_id}")
            self.markers.append({
                "column": column,
                "row": row,
                "card_name": card_name,
                "expires_at": time.monotonic() + 1.0,
            })
            print(f"[queue] {card_name} ({card_id}) tile=({x}, {y})", flush=True)
            changed = True
        now = time.monotonic()
        active_markers = [marker for marker in self.markers if marker["expires_at"] > now]
        if len(active_markers) != len(self.markers):
            self.markers = active_markers
            changed = True
        if changed:
            self.overlay.contentView().setNeedsDisplay_(True)
            self.overlay.contentView().displayIfNeeded()


def main() -> int:
    global RUNNING_APP
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=Path, default=Path(__file__).with_name("arena_grid_profile.json"))
    parser.add_argument("--locked", action="store_true", help="Start in click-through mode.")
    parser.add_argument("--monitor", action="store_true", help="Attach the local queue monitor and highlight queued tiles.")
    parser.add_argument("--target", default="nullsroyale.rel.free", help="Frida process name or PID used with --monitor.")
    args = parser.parse_args()

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    RUNNING_APP = ArenaGridApp(args.profile, args.locked, args.target if args.monitor else None)
    app.activateIgnoringOtherApps_(True)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
