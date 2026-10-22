"""
Jasper UI — On-Screen Real-Time Visualizer
==========================================
Floating, draggable, always-on-top HUD for Jasper.
Features:
- Dark slate/glassmorphism rounded aesthetic
- Listening: Dynamic expanding sonar ripple rings with glowing cyan core
- Processing: Vibrant purple pulsing orbital dots
- Speaking: ChatGPT / Jarvis-style animated undulating equalizer sound waves
- Status label & real-time transcript subtitles
- Thread-safe state communication via Queue
"""

import math
import queue
import sys
import threading
import time
import tkinter as tk
from typing import Optional, Tuple


class JasperUI:
    BG_COLOR = "#090d16"          # Deep cosmic slate / dark glass
    PILL_BORDER = "#1e293b"       # Subtle border slate
    TRANSPARENT_KEY = "#010409"   # Transparent window key
    TEXT_COLOR = "#f8fafc"        # High-contrast white
    MUTED_TEXT = "#64748b"        # Muted slate grey
    ACCENT_CYAN = "#38bdf8"       # Bright cyan-blue glow
    ACCENT_PURPLE = "#c084fc"     # Neon violet/purple
    ACCENT_GREEN = "#34d399"      # Emerald active indicator
    ACCENT_AMBER = "#fbbf24"      # Processing amber

    def __init__(self, state_queue: queue.Queue, width: int = 360, height: int = 100):
        self.state_queue = state_queue
        self.width = width
        self.height = height

        self.current_state = "idle"
        self.current_text = "Say 'Hey Jasper' to begin"
        self.anim_time = 0.0

        # Initialize Tkinter root
        self.root = tk.Tk()
        self.root.title("Jasper HUD")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)

        # Transparency setup for Windows
        try:
            self.root.attributes("-alpha", 0.94)
            self.root.attributes("-transparentcolor", self.TRANSPARENT_KEY)
        except Exception as exc:
            print(f"[WARN] Window transparency not supported: {exc}", file=sys.stderr)

        # Position at bottom-right corner of screen
        try:
            screen_w = self.root.winfo_screenwidth()
            screen_h = self.root.winfo_screenheight()
            pos_x = screen_w - self.width - 40
            pos_y = screen_h - self.height - 60
            self.root.geometry(f"{self.width}x{self.height}+{pos_x}+{pos_y}")
        except Exception:
            self.root.geometry(f"{self.width}x{self.height}+100+100")

        self.root.configure(bg=self.TRANSPARENT_KEY)

        # Inner Canvas
        self.canvas = tk.Canvas(
            self.root,
            width=self.width,
            height=self.height,
            bg=self.TRANSPARENT_KEY,
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack(fill="both", expand=True)

        # Dragging support
        self.canvas.bind("<ButtonPress-1>", self._start_drag)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.drag_x = 0
        self.drag_y = 0

        # Animation states
        self.rings = [0.0, 0.35, 0.70]

    def _start_drag(self, event):
        self.drag_x = event.x
        self.drag_y = event.y

    def _on_drag(self, event):
        x = self.root.winfo_x() - self.drag_x + event.x
        y = self.root.winfo_y() - self.drag_y + event.y
        self.root.geometry(f"+{x}+{y}")

    def _draw_rounded_pill(self):
        """Draw an elegant dark pill container with glowing outline."""
        w, h = self.width, self.height
        r = 20  # Corner radius

        # Rounded rectangle points
        points = [
            r, 2,
            w - r, 2,
            w - 2, 2,
            w - 2, r,
            w - 2, h - r,
            w - 2, h - 2,
            w - r, h - 2,
            r, h - 2,
            2, h - 2,
            2, h - r,
            2, r,
            2, 2,
        ]
        # Smooth polygon pill
        self.canvas.create_polygon(points, fill=self.BG_COLOR, outline=self.PILL_BORDER, width=1.5, smooth=True)

    def _poll_queue(self):
        """Consume messages from the main voice loop thread."""
        while True:
            try:
                msg = self.state_queue.get_nowait()
                if isinstance(msg, tuple) and len(msg) >= 2:
                    self.current_state = str(msg[0]).lower()
                    self.current_text = str(msg[1])
                elif isinstance(msg, str):
                    self.current_state = msg.lower()
                self.state_queue.task_done()
            except queue.Empty:
                break

    def _render_frame(self):
        """Render frame on canvas with state animation."""
        self.canvas.delete("all")
        self._draw_rounded_pill()

        self.anim_time += 0.05
        t = self.anim_time

        # Visualizer Zone Center (Left side)
        cx = 50
        cy = self.height // 2

        # 1. State Visualizer
        if self.current_state == "idle":
            # Idle: Subtle pulsing cyan orb
            pulse = 1.0 + 0.12 * math.sin(t * 2.2)
            r = 9 * pulse
            # Outer faint halo
            self.canvas.create_oval(cx - r - 4, cy - r - 4, cx + r + 4, cy + r + 4,
                                    outline="#0369a1", width=1)
            # Core
            self.canvas.create_oval(cx - r, cy - r, cx + r, cy + r,
                                    fill="#0284c7", outline=self.ACCENT_CYAN, width=1.5)

        elif self.current_state == "listening":
            # Listening: 3 expanding acoustic sonar ripples + bright cyan core
            for i in range(len(self.rings)):
                self.rings[i] = (self.rings[i] + 0.025) % 1.0
                ring_r = 7 + self.rings[i] * 28
                alpha_w = max(0.5, 2.5 * (1.0 - self.rings[i]))
                color = self.ACCENT_CYAN if self.rings[i] < 0.6 else "#0284c7"
                self.canvas.create_oval(
                    cx - ring_r, cy - ring_r, cx + ring_r, cy + ring_r,
                    outline=color, width=max(1, int(alpha_w))
                )
            # Luminous core
            self.canvas.create_oval(cx - 7, cy - 7, cx + 7, cy + 7,
                                    fill=self.ACCENT_CYAN, outline="#ffffff", width=1.5)

        elif self.current_state == "processing":
            # Processing: 3 orbital violet bouncing dots
            for idx in range(3):
                offset_x = cx + (idx - 1) * 14
                pulse = 0.5 + 0.5 * math.sin(t * 6.5 - idx * 0.9)
                dot_r = 3 + pulse * 3.5
                self.canvas.create_oval(
                    offset_x - dot_r, cy - dot_r, offset_x + dot_r, cy + dot_r,
                    fill=self.ACCENT_PURPLE, outline=""
                )

        elif self.current_state == "speaking":
            # Speaking: 7 fluid undulating equalizer sound bars (ChatGPT style)
            bar_count = 7
            bar_w = 4
            spacing = 5
            total_w = bar_count * bar_w + (bar_count - 1) * spacing
            start_x = cx - (total_w // 2)

            for i in range(bar_count):
                x = start_x + i * (bar_w + spacing)
                # Combine two sine frequencies for organic fluid speech motion
                wave1 = math.sin(t * 8.0 + i * 0.8)
                wave2 = math.cos(t * 5.0 - i * 0.5)
                amp = (abs(wave1) * 0.7 + abs(wave2) * 0.3)
                h_val = 6 + amp * 28

                # Dual-tone gradient illusion (outer bars violet, center bars cyan)
                color = self.ACCENT_CYAN if 1 <= i <= 5 else self.ACCENT_PURPLE
                self.canvas.create_rectangle(
                    x, cy - h_val / 2, x + bar_w, cy + h_val / 2,
                    fill=color, outline=""
                )

        # 2. Text / Status Zone (Right side)
        text_x = 98

        state_label = self.current_state.upper()
        if self.current_state == "idle":
            state_color = self.MUTED_TEXT
        elif self.current_state == "listening":
            state_color = self.ACCENT_CYAN
        elif self.current_state == "processing":
            state_color = self.ACCENT_AMBER
        elif self.current_state == "speaking":
            state_color = self.ACCENT_GREEN
        else:
            state_color = self.ACCENT_CYAN

        # Small status badge text
        self.canvas.create_text(
            text_x, 26,
            text=f"● {state_label}",
            fill=state_color,
            font=("Consolas", 8, "bold"),
            anchor="w",
        )

        # Primary transcript / subtitle text
        display_str = self.current_text
        if len(display_str) > 34:
            display_str = display_str[:31] + "..."

        self.canvas.create_text(
            text_x, 52,
            text=display_str,
            fill=self.TEXT_COLOR,
            font=("Segoe UI", 10, "bold"),
            anchor="w",
        )

    def run(self):
        """Start animation loop and Tkinter mainloop."""
        def _loop():
            # The reschedule MUST happen even if a frame raises, otherwise one
            # bad frame permanently freezes the HUD.
            try:
                self._poll_queue()
                self._render_frame()
            except Exception as exc:
                print(f"[ERROR] UI render frame failed: {exc}", file=sys.stderr)
            finally:
                try:
                    self.root.after(30, _loop)
                except Exception as exc:
                    print(f"[ERROR] UI reschedule failed: {exc}", file=sys.stderr)

        self.root.after(30, _loop)
        self.root.mainloop()


def start_ui_thread(ui_queue: queue.Queue) -> Optional[threading.Thread]:
    """
    Launch Jasper UI overlay on a background daemon thread.
    Returns Thread instance or None on failure.
    """
    try:
        def _target():
            ui = JasperUI(ui_queue)
            ui.run()

        ui_thread = threading.Thread(target=_target, daemon=True, name="JasperUIThread")
        ui_thread.start()
        print("[INIT] Jasper HUD overlay initialized.")
        return ui_thread
    except Exception as exc:
        print(f"[WARN] Failed to start Jasper UI overlay: {exc}", file=sys.stderr)
        return None
