import math
import queue
import sys
import threading
import time
import tkinter as tk
from typing import Optional, Tuple


class JasperUI:
    """
    Floating dark overlay UI for Jasper voice assistant.
    Features dark glass pill design, real-time animated state visualizer (idle, listening, processing, speaking),
    and status text updates.
    """

    BG_COLOR = "#0f172a"          # Dark slate / navy glass pill background
    TRANSPARENT_KEY = "#000001"   # Key color for transparent window background
    TEXT_COLOR = "#f8fafc"        # High-contrast white text
    MUTED_TEXT = "#94a3b8"        # Slate grey subtitle text
    ACCENT_BLUE = "#38bdf8"       # Bright cyan-blue glow
    ACCENT_PURPLE = "#a855f7"     # Vibrant purple
    ACCENT_GREEN = "#22c55e"      # Status green

    def __init__(self, state_queue: queue.Queue, width: int = 320, height: int = 110):
        self.state_queue = state_queue
        self.width = width
        self.height = height

        self.current_state = "idle"
        self.current_text = "Say 'Hey Jasper'"
        self.anim_time = 0.0

        # Create Tk root
        self.root = tk.Tk()
        self.root.title("Jasper Assistant")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)

        # Transparency setup on Windows
        try:
            self.root.attributes("-alpha", 0.92)
            self.root.attributes("-transparentcolor", self.TRANSPARENT_KEY)
        except Exception:
            pass

        # Position in bottom-right corner
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        pos_x = screen_w - self.width - 30
        pos_y = screen_h - self.height - 70
        self.root.geometry(f"{self.width}x{self.height}+{pos_x}+{pos_y}")

        # Main window container
        self.root.configure(bg=self.TRANSPARENT_KEY)

        # Inner canvas for pill background & animations
        self.canvas = tk.Canvas(
            self.root,
            width=self.width,
            height=self.height,
            bg=self.TRANSPARENT_KEY,
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack(fill="both", expand=True)

        # Allow dragging the overlay with mouse
        self.canvas.bind("<ButtonPress-1>", self._start_drag)
        self.canvas.bind("<B1-Motion>", self._on_drag)

        self.drag_x = 0
        self.drag_y = 0

        # Sonar rings state for listening
        self.rings = [0.0, 0.33, 0.66]

    def _start_drag(self, event):
        self.drag_x = event.x
        self.drag_y = event.y

    def _on_drag(self, event):
        x = self.root.winfo_x() - self.drag_x + event.x
        y = self.root.winfo_y() - self.drag_y + event.y
        self.root.geometry(f"+{x}+{y}")

    def _draw_pill_background(self):
        """Draw rounded dark slate pill canvas background with border."""
        r = 20  # Corner radius
        w, h = self.width, self.height
        # Outer border / glow
        self.canvas.create_rectangle(1, 1, w - 1, h - 1, fill=self.BG_COLOR, outline="#334155", width=1)

    def _poll_queue(self):
        """Check for state/text updates from the voice loop."""
        while True:
            try:
                msg = self.state_queue.get_nowait()
                if isinstance(msg, tuple) and len(msg) >= 2:
                    self.current_state = msg[0]
                    self.current_text = str(msg[1])
                elif isinstance(msg, str):
                    self.current_state = msg
                self.state_queue.task_done()
            except queue.Empty:
                break

    def _render_frame(self):
        """Render current animation frame on Canvas based on state."""
        self.canvas.delete("all")
        self._draw_pill_background()

        self.anim_time += 0.04
        t = self.anim_time

        # Draw Visualizer Zone (Left side)
        cx, cy = 45, self.height // 2

        if self.current_state == "idle":
            # Idle: Dim cyan dot with subtle breathing pulse
            scale = 1.0 + 0.15 * math.sin(t * 2.0)
            r = 10 * scale
            self.canvas.create_oval(cx - r, cy - r, cx + r, cy + r, fill="#0284c7", outline=self.ACCENT_BLUE, width=1.5)

        elif self.current_state == "listening":
            # Listening: Glowing center + 3 expanding sonar rings
            for i in range(len(self.rings)):
                self.rings[i] = (self.rings[i] + 0.02) % 1.0
                ring_r = 8 + self.rings[i] * 24
                alpha_width = max(0.5, 2.5 * (1.0 - self.rings[i]))
                self.canvas.create_oval(
                    cx - ring_r, cy - ring_r, cx + ring_r, cy + ring_r,
                    outline=self.ACCENT_BLUE, width=int(alpha_width)
                )
            # Center core
            self.canvas.create_oval(cx - 7, cy - 7, cx + 7, cy + 7, fill=self.ACCENT_BLUE, outline="#ffffff")

        elif self.current_state == "processing":
            # Processing: 3 pulsing dots (typing indicator effect)
            for idx in range(3):
                offset_x = cx + (idx - 1) * 14
                pulse = 0.5 + 0.5 * math.sin(t * 6.0 - idx * 0.8)
                dot_r = 3 + pulse * 3
                self.canvas.create_oval(
                    offset_x - dot_r, cy - dot_r, offset_x + dot_r, cy + dot_r,
                    fill=self.ACCENT_PURPLE, outline=""
                )

        elif self.current_state == "speaking":
            # Speaking: 5 undulating equalizer bars (ChatGPT-style wave)
            bar_count = 5
            bar_w = 4
            spacing = 7
            start_x = cx - ((bar_count * (bar_w + spacing)) // 2)

            for i in range(bar_count):
                x = start_x + i * (bar_w + spacing)
                # Phase offset wave height calculation
                h_val = 6 + 18 * abs(math.sin(t * 7.0 + i * 0.75))
                self.canvas.create_rectangle(
                    x, cy - h_val / 2, x + bar_w, cy + h_val / 2,
                    fill=self.ACCENT_BLUE, outline=""
                )

        # Draw Status & Response Text Zone (Right side)
        text_x = 90
        state_label = self.current_state.upper()
        if self.current_state == "idle":
            state_color = self.MUTED_TEXT
        elif self.current_state == "listening":
            state_color = self.ACCENT_BLUE
        elif self.current_state == "processing":
            state_color = self.ACCENT_PURPLE
        else:
            state_color = self.ACCENT_GREEN

        # State pill indicator
        self.canvas.create_text(text_x, 28, text=state_label, fill=state_color, font=("Consolas", 8, "bold"), anchor="w")

        # Main subtitle / transcript text (truncated gracefully)
        display_str = self.current_text
        if len(display_str) > 32:
            display_str = display_str[:29] + "..."

        self.canvas.create_text(text_x, 54, text=display_str, fill=self.TEXT_COLOR, font=("Segoe UI", 10, "bold"), anchor="w")

    def run(self):
        """Start animation loop and Tkinter mainloop."""
        def _loop():
            self._poll_queue()
            self._render_frame()
            self.root.after(30, _loop)

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

        ui_thread = threading.Thread(target=_target, daemon=True)
        ui_thread.start()
        print("[INFO] Jasper UI overlay thread started.")
        return ui_thread
    except Exception as exc:
        print(f"[WARN] Failed to start Jasper UI overlay: {exc}", file=sys.stderr)
        return None
