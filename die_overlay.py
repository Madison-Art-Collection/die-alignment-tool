#!/usr/bin/env python3
"""
Die comparison overlay tool.

Overlay two coin photographs to compare dies visually.

Usage:
    python die_overlay.py [--coin-a PATH] [--coin-b PATH]

Controls:
    Load Coin A  — set the base (reference) image
    Load Coin B  — set the overlay image; auto-fits on load
    Sliders      — scale, rotation, transparency
    Drag         — pan coin B over coin A
    Flip         — mirror coin B horizontally
    Manual Align — 4–8 matched point pairs for precise alignment
"""

import sys
import argparse
import tkinter as tk
from tkinter import ttk, filedialog
from pathlib import Path
import numpy as np
from PIL import Image, ImageTk
import cv2

CANVAS_SIZE = 700


# ── image utilities ───────────────────────────────────────────────────────────

def segment_dark_bg(pil_image: Image.Image) -> Image.Image:
    """
    Remove dark/felt background from a coin photo.
    Returns RGBA image: coin pixels opaque, background transparent.
    Tries both normal and inverted thresholding to handle different backgrounds.
    """
    img_np  = np.array(pil_image.convert("RGB"))
    gray    = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY)
    blurred = cv2.GaussianBlur(gray, (21, 21), 0)
    _, thresh = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    
    # Try to find contours in the thresholded image
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    # If no contours or very small contours, try inverted threshold
    if not contours or max(cv2.contourArea(c) for c in contours) < (gray.size * 0.05):
        _, thresh = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    if not contours:
        # No contours found at all, return with simple threshold-based mask
        _, simple_mask = cv2.threshold(gray, gray.mean(), 255, cv2.THRESH_BINARY)
        rgba = np.array(pil_image.convert("RGBA"))
        rgba[:, :, 3] = simple_mask
        return Image.fromarray(rgba)
    
    largest = max(contours, key=cv2.contourArea)
    mask = np.zeros(gray.shape, dtype=np.uint8)
    cv2.drawContours(mask, [largest], -1, 255, thickness=cv2.FILLED)
    
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
    mask   = cv2.dilate(mask, kernel, iterations=2)
    rgba = np.array(pil_image.convert("RGBA"))
    rgba[:, :, 3] = mask
    return Image.fromarray(rgba)


def apply_alpha(rgba_img: Image.Image, alpha: float) -> Image.Image:
    """Scale the alpha channel by alpha (0.0–1.0)."""
    r, g, b, a = rgba_img.split()
    a = a.point(lambda v: int(v * alpha))
    return Image.merge("RGBA", (r, g, b, a))



# ── main GUI ──────────────────────────────────────────────────────────────────

class OverlayApp:
    def __init__(self, root: tk.Tk, coin_a_path: str = None, coin_b_path: str = None):
        self.root = root
        self.root.title("Die Comparison Overlay")
        self.root.resizable(False, False)

        # Internal state
        self._coin_a_img  : Image.Image | None = None  # RGB — base/reference
        self._target_rgba : Image.Image | None = None  # RGBA — overlay (coin B), segmented
        self._target_rgb_original : Image.Image | None = None  # Original RGB of coin B before segmentation
        self._photo_ref   = None
        self._drag_anchor = None
        self._drag_offset_start = None
        
        # Cache for performance optimization
        self._cached_base = None      # Cached resized base image
        self._cached_overlay = None   # Cached transformed overlay (before alpha)
        self._last_transform = None   # Last transform parameters

        # Control variables
        self.scale_var    = tk.DoubleVar(value=1.0)
        self.rotation_var = tk.DoubleVar(value=0.0)
        self.alpha_var    = tk.DoubleVar(value=0.55)
        self.x_off_var    = tk.IntVar(value=0)
        self.y_off_var    = tk.IntVar(value=0)
        self.flip_var     = tk.BooleanVar(value=False)
        self.invert_var   = tk.BooleanVar(value=False)
        self.use_segmentation_var = tk.BooleanVar(value=False)

        self._coin_a_label = tk.StringVar(value="(none)")
        self._coin_b_label = tk.StringVar(value="(none)")

        self._build_ui()

        if coin_a_path:
            self._load_coin_a(coin_a_path)
        if coin_b_path:
            self._load_coin_b(coin_b_path)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        ctrl = ttk.Frame(self.root, padding=(10, 10, 0, 10))
        ctrl.pack(side=tk.LEFT, fill=tk.Y)

        # Coin A loader
        ttk.Label(ctrl, text="Coin A  (base / reference)",
                  font=("TkDefaultFont", 9, "bold")).pack(anchor=tk.W)
        ttk.Label(ctrl, textvariable=self._coin_a_label,
                  foreground="#888", font=("TkSmallCaptionFont", 9),
                  wraplength=190).pack(anchor=tk.W, pady=(0, 2))
        ttk.Button(ctrl, text="Load Coin A…",
                   command=self._browse_coin_a).pack(fill=tk.X, pady=(0, 8))

        # Coin B loader
        ttk.Label(ctrl, text="Coin B  (overlay)",
                  font=("TkDefaultFont", 9, "bold")).pack(anchor=tk.W)
        ttk.Label(ctrl, textvariable=self._coin_b_label,
                  foreground="#888", font=("TkSmallCaptionFont", 9),
                  wraplength=190).pack(anchor=tk.W, pady=(0, 2))
        ttk.Button(ctrl, text="Load Coin B…",
                   command=self._browse_coin_b).pack(fill=tk.X, pady=(0, 8))

        ttk.Separator(ctrl, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=6)

        # Transform controls
        self._spin_row(ctrl, "Scale",         self.scale_var,    0.001, 0.05, 6.0,    "{:.4f}")
        self._spin_row(ctrl, "Rotation (°)",  self.rotation_var, 0.1,  -180.0, 180.0, "{:.1f}")
        self._spin_row(ctrl, "X offset (px)", self.x_off_var,    1,    -800, 800,     "{:.0f}")
        self._spin_row(ctrl, "Y offset (px)", self.y_off_var,    1,    -800, 800,     "{:.0f}")

        ttk.Label(ctrl, text="Transparency").pack(anchor=tk.W, pady=(8, 0))
        ttk.Scale(ctrl, from_=0.0, to=1.0, variable=self.alpha_var,
                  orient=tk.HORIZONTAL, length=200,
                  command=lambda _: self._redraw()).pack(fill=tk.X)

        ttk.Separator(ctrl, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8)

        ttk.Checkbutton(ctrl, text="Flip coin B (horizontal)",
                        variable=self.flip_var, command=self._redraw).pack(anchor=tk.W)
        ttk.Checkbutton(ctrl, text="Swap base/overlay",
                        variable=self.invert_var, command=self._redraw).pack(anchor=tk.W, pady=(4, 0))
        ttk.Checkbutton(ctrl, text="Remove background (segmentation)",
                        variable=self.use_segmentation_var, command=self._reload_coin_b).pack(anchor=tk.W, pady=(4, 0))

        ttk.Separator(ctrl, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8)

        ttk.Button(ctrl, text="Manual Align (4+ pts)",
                   command=self._open_align_dialog).pack(fill=tk.X, pady=2)
        ttk.Button(ctrl, text="Auto-fit",
                   command=lambda: [self._auto_fit(), self._redraw()]).pack(fill=tk.X, pady=2)
        ttk.Button(ctrl, text="Reset transform",
                   command=self._reset).pack(fill=tk.X, pady=2)

        ttk.Separator(ctrl, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8)

        hint = ("Drag        → pan coin B\n"
                "↑↓←→        → nudge 1 px\n"
                "Shift+arrow → nudge 5 px\n"
                "= / -       → scale ±0.001\n"
                "+ / _       → scale ±0.01")
        ttk.Label(ctrl, text=hint, foreground="grey",
                  font=("TkSmallCaptionFont", 9)).pack(anchor=tk.W)

        # Canvas
        canvas_frame = ttk.Frame(self.root, padding=10)
        canvas_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(canvas_frame,
                                width=CANVAS_SIZE, height=CANVAS_SIZE,
                                bg="#111111", cursor="fleur",
                                highlightthickness=0)
        self.canvas.pack()
        self._canvas_img_id = self.canvas.create_image(0, 0, anchor=tk.NW)

        self.status_var = tk.StringVar(value="Load Coin A and Coin B to begin")
        ttk.Label(canvas_frame, textvariable=self.status_var,
                  foreground="grey").pack(pady=(4, 0))

        # Event bindings
        self.canvas.bind("<ButtonPress-1>",   self._drag_start)
        self.canvas.bind("<B1-Motion>",       self._drag_motion)
        self.canvas.bind("<ButtonRelease-1>", self._drag_end)

        self.root.bind("<Left>",  lambda e: self._nudge(-1, 0, e))
        self.root.bind("<Right>", lambda e: self._nudge( 1, 0, e))
        self.root.bind("<Up>",    lambda e: self._nudge( 0,-1, e))
        self.root.bind("<Down>",  lambda e: self._nudge( 0, 1, e))

        self.root.bind("<equal>",      lambda e: self._scale_step( 0.001))
        self.root.bind("<plus>",       lambda e: self._scale_step( 0.01))
        self.root.bind("<minus>",      lambda e: self._scale_step(-0.001))
        self.root.bind("<underscore>", lambda e: self._scale_step(-0.01))

    def _spin_row(self, parent, label: str, var,
                  step: float, min_val: float, max_val: float, fmt: str):
        """Entry field flanked by − and + buttons for precise value control."""
        ttk.Label(parent, text=label).pack(anchor=tk.W, pady=(8, 0))
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=(2, 0))

        def _clamp(v):
            return max(min_val, min(max_val, v))

        entry_str = tk.StringVar()

        def _sync_to_entry(*_):
            entry_str.set(fmt.format(var.get()))

        def _commit(event=None):
            try:
                val = float(entry_str.get())
                var.set(_clamp(val))
            except ValueError:
                pass
            _sync_to_entry()
            self._redraw()

        def _step(delta):
            var.set(_clamp(round(var.get() + delta, 10)))
            _sync_to_entry()
            self._redraw()

        var.trace_add("write", _sync_to_entry)
        _sync_to_entry()

        ttk.Button(row, text="−", width=2, command=lambda: _step(-step)).pack(side=tk.LEFT)
        ent = ttk.Entry(row, textvariable=entry_str, width=10, justify=tk.CENTER)
        ent.pack(side=tk.LEFT, padx=3)
        ent.bind("<Return>",   _commit)
        ent.bind("<FocusOut>", _commit)
        ttk.Button(row, text="+", width=2, command=lambda: _step(step)).pack(side=tk.LEFT)

    # ── data loading ──────────────────────────────────────────────────────────

    def _load_coin_a(self, path: str):
        path = Path(path)
        self._coin_a_img = Image.open(path).convert("RGB")
        self._coin_a_label.set(path.name)
        self.status_var.set(f"Coin A loaded: {path.name}")
        self._invalidate_cache()
        self._try_auto_align()

    def _load_coin_b(self, path: str):
        path = Path(path)
        if self.use_segmentation_var.get():
            self.status_var.set(f"Segmenting {path.name} …")
            self.root.update_idletasks()
        raw = Image.open(path).convert("RGB")
        self._target_rgb_original = raw.copy()  # Keep original before segmentation
        
        if self.use_segmentation_var.get():
            self._target_rgba = segment_dark_bg(raw)
        else:
            # No segmentation - convert to RGBA with full opacity
            self._target_rgba = raw.convert("RGBA")
        
        self._coin_b_label.set(path.name)
        self.status_var.set(f"Coin B loaded: {path.name}")
        self._invalidate_cache()
        self._try_auto_align()
    
    def _reload_coin_b(self):
        """Reload Coin B with current segmentation setting."""
        if self._target_rgb_original is not None:
            if self.use_segmentation_var.get():
                self._target_rgba = segment_dark_bg(self._target_rgb_original)
            else:
                self._target_rgba = self._target_rgb_original.convert("RGBA")
            self._invalidate_cache()
            self._redraw()

    def _try_auto_align(self):
        """Auto-fit coin B to canvas when both coins are loaded."""
        if self._coin_a_img is None or self._target_rgba is None:
            return
        self._auto_fit()
        self._redraw()
        self.status_var.set("Both coins loaded — drag or use Manual Align to refine")

    def _browse_coin_a(self):
        path = filedialog.askopenfilename(
            title="Select Coin A (base / reference)",
            filetypes=[("Images", "*.jpg *.jpeg *.png *.tif *.tiff"), ("All files", "*.*")],
        )
        if path:
            self._load_coin_a(path)

    def _browse_coin_b(self):
        path = filedialog.askopenfilename(
            title="Select Coin B (overlay)",
            filetypes=[("Images", "*.jpg *.jpeg *.png *.tif *.tiff"), ("All files", "*.*")],
        )
        if path:
            self._load_coin_b(path)

    # ── cache invalidation ────────────────────────────────────────────────────
    
    def _invalidate_cache(self):
        """Clear cached transforms when images change."""
        self._cached_base = None
        self._cached_overlay = None
        self._last_transform = None

    # ── auto-fit ──────────────────────────────────────────────────────────────

    def _auto_fit(self):
        """Scale and center coin B so its coin region fills ~90% of the canvas."""
        if self._target_rgba is None:
            return

        alpha = np.array(self._target_rgba.split()[3])
        rows  = np.any(alpha > 10, axis=1)
        cols  = np.any(alpha > 10, axis=0)
        if not rows.any() or not cols.any():
            return

        rmin, rmax = np.where(rows)[0][[0, -1]]
        cmin, cmax = np.where(cols)[0][[0, -1]]

        coin_w  = cmax - cmin
        coin_h  = rmax - rmin
        coin_cx = (cmin + cmax) / 2
        coin_cy = (rmin + rmax) / 2

        scale    = (CANVAS_SIZE * 0.90) / max(coin_w, coin_h)
        scaled_w = self._target_rgba.width  * scale
        scaled_h = self._target_rgba.height * scale
        default_x = (CANVAS_SIZE - scaled_w)  / 2
        default_y = (CANVAS_SIZE - scaled_h) / 2
        x_off = int(CANVAS_SIZE / 2 - (default_x + coin_cx * scale))
        y_off = int(CANVAS_SIZE / 2 - (default_y + coin_cy * scale))

        self.scale_var.set(round(scale, 3))
        self.rotation_var.set(0.0)
        self.x_off_var.set(x_off)
        self.y_off_var.set(y_off)
        self.flip_var.set(False)

    # ── compositing & drawing ─────────────────────────────────────────────────

    def _redraw(self, *_):
        if self._coin_a_img is None or self._target_rgba is None:
            return

        # Current transform parameters
        current_transform = (
            self.scale_var.get(),
            self.rotation_var.get(),
            self.x_off_var.get(),
            self.y_off_var.get(),
            self.flip_var.get(),
            self.invert_var.get()
        )
        
        # Check if only alpha changed (optimization for transparency slider)
        only_alpha_changed = (
            self._last_transform is not None and
            current_transform == self._last_transform
        )
        
        if only_alpha_changed and self._cached_base and self._cached_overlay:
            # Fast path: only transparency changed, reuse cached transforms
            base = self._cached_base
            tgt = self._cached_overlay.copy()
            cx = (CANVAS_SIZE - tgt.width)  // 2 + self.x_off_var.get()
            cy = (CANVAS_SIZE - tgt.height) // 2 + self.y_off_var.get()
        else:
            # Full transform needed
            # Coin A fills canvas as base, preserving aspect ratio
            img_a = self._coin_a_img
            aspect = img_a.width / img_a.height
            if aspect > 1:  # Wider than tall
                new_w = CANVAS_SIZE
                new_h = int(CANVAS_SIZE / aspect)
            else:  # Taller than wide or square
                new_h = CANVAS_SIZE
                new_w = int(CANVAS_SIZE * aspect)
            
            resized_a = img_a.resize((new_w, new_h), Image.LANCZOS).convert("RGBA")
            # Center on canvas
            base = Image.new("RGBA", (CANVAS_SIZE, CANVAS_SIZE), (17, 17, 17, 255))
            paste_x = (CANVAS_SIZE - new_w) // 2
            paste_y = (CANVAS_SIZE - new_h) // 2
            base.paste(resized_a, (paste_x, paste_y))
            self._cached_base = base

            # Coin B is the transformed overlay
            tgt = self._target_rgba.copy()

            if self.flip_var.get():
                tgt = tgt.transpose(Image.FLIP_LEFT_RIGHT)

            s = self.scale_var.get()
            tgt = tgt.resize((max(1, int(tgt.width * s)), max(1, int(tgt.height * s))),
                             Image.LANCZOS)

            angle = self.rotation_var.get()
            if angle:
                tgt = tgt.rotate(-angle, expand=True, resample=Image.BICUBIC)

            self._cached_overlay = tgt.copy()
            self._last_transform = current_transform
            
            cx = (CANVAS_SIZE - tgt.width)  // 2 + self.x_off_var.get()
            cy = (CANVAS_SIZE - tgt.height) // 2 + self.y_off_var.get()

        # Apply alpha (this is done every time, but it's fast)
        tgt = apply_alpha(tgt, self.alpha_var.get())

        if self.invert_var.get():
            # Swap: coin B is base, coin A is the semi-transparent overlay
            tgt_base = tgt.copy()
            r, g, b, a = tgt_base.split()
            a = a.point(lambda v: 255 if v > 0 else 0)
            tgt_base = Image.merge("RGBA", (r, g, b, a))
            canvas = Image.new("RGBA", (CANVAS_SIZE, CANVAS_SIZE), (17, 17, 17, 255))
            canvas.paste(tgt_base, (cx, cy), tgt_base)
            cat_overlay = apply_alpha(base, self.alpha_var.get())
            canvas.alpha_composite(cat_overlay)
        else:
            canvas = base.copy()
            canvas.paste(tgt, (cx, cy), tgt)

        img_rgb = canvas.convert("RGB")
        self._photo_ref = ImageTk.PhotoImage(img_rgb)
        self.canvas.itemconfig(self._canvas_img_id, image=self._photo_ref)

    # ── interaction ───────────────────────────────────────────────────────────

    def _drag_start(self, event):
        self._drag_anchor       = (event.x, event.y)
        self._drag_offset_start = (self.x_off_var.get(), self.y_off_var.get())

    def _drag_motion(self, event):
        if self._drag_anchor is None:
            return
        dx = event.x - self._drag_anchor[0]
        dy = event.y - self._drag_anchor[1]
        self.x_off_var.set(self._drag_offset_start[0] + dx)
        self.y_off_var.set(self._drag_offset_start[1] + dy)
        self._redraw()

    def _drag_end(self, _event):
        self._drag_anchor = None

    def _scale_step(self, delta: float):
        self.scale_var.set(round(max(0.05, min(6.0, self.scale_var.get() + delta)), 6))
        self._redraw()

    def _nudge(self, dx, dy, event):
        step = 5 if (event.state & 0x1) else 1   # Shift = 5 px
        self.x_off_var.set(self.x_off_var.get() + dx * step)
        self.y_off_var.set(self.y_off_var.get() + dy * step)
        self._redraw()

    def _reset(self):
        if self._target_rgba is None:
            return
        auto = CANVAS_SIZE / max(self._target_rgba.size) * 0.88
        self.scale_var.set(round(auto, 2))
        self.rotation_var.set(0.0)
        self.alpha_var.set(0.55)
        self.x_off_var.set(0)
        self.y_off_var.set(0)
        self.flip_var.set(False)
        self.invert_var.set(False)
        self._redraw()

    # ── manual alignment dialog ───────────────────────────────────────────────

    def _open_align_dialog(self):
        """
        Manual 4–8 point alignment dialog.

        Workflow: INTERLEAVED — place coin A point N, then matching coin B
        point N, for N = 1 … pairs.  Both canvases always accept clicks;
        clicking the wrong panel shows a clear error.
        """
        if self._coin_a_img is None or self._target_rgba is None:
            self.status_var.set("⚠  Load both coins first")
            return

        PSIZE      = 620
        GAP        = 8
        DOT_R      = 9
        MIN_PAIRS  = 4
        MAX_PAIRS  = 8
        MIN_SPREAD = 30
        PAIR_COLORS = [
            "#FF4444", "#44CC44", "#4499FF", "#FFD700",
            "#FF88FF", "#00FFCC", "#FF8800", "#AAFFAA",
        ]

        dlg = tk.Toplevel(self.root)
        dlg.title("Manual Alignment — click matching point pairs")
        dlg.resizable(False, False)
        dlg.grab_set()

        def fit_to_panel(img_pil: Image.Image):
            s      = min(PSIZE / img_pil.width, PSIZE / img_pil.height) * 0.96
            nw, nh = int(img_pil.width * s), int(img_pil.height * s)
            ox, oy = (PSIZE - nw) // 2, (PSIZE - nh) // 2
            panel  = Image.new("RGB", (PSIZE, PSIZE), (28, 28, 28))
            panel.paste(img_pil.convert("RGB").resize((nw, nh), Image.LANCZOS), (ox, oy))
            return panel, s, ox, oy

        try:
            cat_base, cat_s, cat_ox, cat_oy = fit_to_panel(self._coin_a_img)
            
            # For Coin B, use the original RGB image (no segmentation needed for alignment)
            tgt_bg = self._target_rgb_original if self._target_rgb_original else self._target_rgba.convert("RGB")
            tgt_base, tgt_s, tgt_ox, tgt_oy = fit_to_panel(tgt_bg)
        except Exception as exc:
            dlg.destroy()
            self.status_var.set(f"⚠  Could not prepare alignment images: {exc}")
            return

        # pairs[i] = {"cat": (px,py,ix,iy) or None, "tgt": same}
        pairs = [{"cat": None, "tgt": None} for _ in range(MIN_PAIRS)]
        state = {"next": (0, "cat")}

        canv_l = tk.Canvas(dlg, width=PSIZE, height=PSIZE,
                           bg="#1c1c1c", cursor="crosshair", highlightthickness=0)
        canv_r = tk.Canvas(dlg, width=PSIZE, height=PSIZE,
                           bg="#1c1c1c", cursor="crosshair", highlightthickness=0)
        canv_l.grid(row=0, column=0, padx=(10, GAP // 2), pady=(10, 4))
        canv_r.grid(row=0, column=1, padx=(GAP // 2, 10), pady=(10, 4))

        tk.Label(dlg, text="COIN A  (reference)",
                 font=("TkDefaultFont", 10, "bold"), fg="#aaa").grid(row=1, column=0)
        tk.Label(dlg, text="COIN B  (overlay)",
                 font=("TkDefaultFont", 10, "bold"), fg="#aaa").grid(row=1, column=1)

        bot = tk.Frame(dlg)
        bot.grid(row=2, column=0, columnspan=2, sticky="ew", padx=10, pady=(4, 10))
        status_lbl = tk.Label(bot, text="", anchor=tk.W, width=52,
                              font=("TkDefaultFont", 10))
        status_lbl.pack(side=tk.LEFT)

        compute_btn = ttk.Button(bot, text="Compute alignment",
                                 command=lambda: _compute(), state="disabled")
        compute_btn.pack(side=tk.RIGHT, padx=4)
        add_btn = ttk.Button(bot, text="+ Add point",
                             command=lambda: _add_pair(), state="disabled")
        add_btn.pack(side=tk.RIGHT, padx=4)
        ttk.Button(bot, text="Undo",  command=lambda: _undo()).pack(side=tk.RIGHT, padx=4)
        ttk.Button(bot, text="Reset", command=lambda: _reset_dlg()).pack(side=tk.RIGHT)

        from PIL import ImageDraw as _ImageDraw
        _photos = [None, None]

        def _draw():
            nxt = state["next"]
            if nxt == "ready":
                next_pair, next_side = len(pairs), None
            else:
                next_pair, next_side = nxt

            def annotate(base, side_key, active):
                img = base.copy()
                d   = _ImageDraw.Draw(img)
                border_color = "#FFE040" if active else "#444444"
                d.rectangle([2, 2, PSIZE-3, PSIZE-3], outline=border_color, width=4)
                for i, p in enumerate(pairs):
                    pt = p[side_key]
                    if pt is None:
                        continue
                    px, py = pt[0], pt[1]
                    col = PAIR_COLORS[i]
                    d.ellipse([px-DOT_R, py-DOT_R, px+DOT_R, py+DOT_R],
                              outline=col, width=3)
                    d.ellipse([px-3, py-3, px+3, py+3], fill=col)
                    d.text((px + DOT_R + 4, py - DOT_R - 2), str(i + 1), fill=col)
                if active and next_pair < len(PAIR_COLORS):
                    col = PAIR_COLORS[next_pair]
                    d.text((12, 12), f"→ click {next_pair + 1}", fill=col)
                return img

            _photos[0] = ImageTk.PhotoImage(annotate(cat_base, "cat", next_side == "cat"))
            _photos[1] = ImageTk.PhotoImage(annotate(tgt_base, "tgt", next_side == "tgt"))
            canv_l.delete("all")
            canv_l.create_image(0, 0, anchor=tk.NW, image=_photos[0])
            canv_r.delete("all")
            canv_r.create_image(0, 0, anchor=tk.NW, image=_photos[1])

        def _set_status(msg: str, color: str = "white"):
            status_lbl.config(text=msg, fg=color)

        def _n_complete():
            return sum(1 for p in pairs if p["cat"] and p["tgt"])

        def _update_status():
            nxt = state["next"]
            if nxt == "ready":
                n = _n_complete()
                add_btn.config(state="normal" if n < MAX_PAIRS else "disabled")
                compute_btn.config(state="normal")
                _set_status(
                    f"{n} pairs placed — add more for better accuracy or compute now",
                    "#88FF88")
            else:
                pi, side = nxt
                add_btn.config(state="disabled")
                compute_btn.config(state="disabled")
                col   = PAIR_COLORS[pi % len(PAIR_COLORS)]
                panel = "COIN A (left)" if side == "cat" else "COIN B (right)"
                done  = _n_complete()
                _set_status(
                    f"Pair {pi+1}/{len(pairs)}  —  click on {panel}   ({done} complete)",
                    col)

        def _too_close(px, py, existing_pts) -> bool:
            return any(
                abs(px - ep[0]) < MIN_SPREAD and abs(py - ep[1]) < MIN_SPREAD
                for ep in existing_pts if ep is not None
            )

        def _points_collinear(pts) -> bool:
            if any(p is None for p in pts):
                return False
            coords = [(p[2], p[3]) for p in pts]
            x1, y1 = coords[0]
            for (x2, y2), (x3, y3) in zip(coords[1:], coords[2:]):
                area  = abs((x2-x1)*(y3-y1) - (x3-x1)*(y2-y1))
                scale = max(abs(x2-x1), abs(x3-x1), abs(y2-y1), abs(y3-y1), 1)
                if area / scale >= 5:
                    return False
            return True

        def _handle_click(clicked_side: str, event):
            nxt = state["next"]
            if nxt == "ready":
                _set_status("Press '+ Add point' to add another pair, or 'Compute'",
                            "#FF9900")
                return

            pi, expected_side = nxt

            if clicked_side != expected_side:
                correct = "COIN A (left)" if expected_side == "cat" else "COIN B (right)"
                _set_status(f"⚠  Click on {correct}  ←", "#FF9900")
                dlg.after(800, _update_status)
                return

            ox = cat_ox if clicked_side == "cat" else tgt_ox
            oy = cat_oy if clicked_side == "cat" else tgt_oy
            s  = cat_s  if clicked_side == "cat" else tgt_s
            px, py = event.x, event.y
            ix, iy = (px - ox) / s, (py - oy) / s

            existing = [p[clicked_side] for p in pairs if p[clicked_side] is not None]
            if _too_close(px, py, existing):
                _set_status("⚠  Too close to an existing point — pick a distinct feature",
                            "#FF9900")
                return

            pairs[pi][clicked_side] = (px, py, ix, iy)

            if clicked_side == "cat":
                state["next"] = (pi, "tgt")
            else:
                next_pi = pi + 1
                state["next"] = (next_pi, "cat") if next_pi < len(pairs) else "ready"

            _update_status()
            _draw()

        canv_l.bind("<Button-1>", lambda e: _handle_click("cat", e))
        canv_r.bind("<Button-1>", lambda e: _handle_click("tgt", e))

        def _add_pair():
            if _n_complete() >= MAX_PAIRS:
                return
            pairs.append({"cat": None, "tgt": None})
            state["next"] = (len(pairs) - 1, "cat")
            _update_status()
            _draw()

        def _undo():
            nxt = state["next"]
            if nxt == "ready":
                for i in range(len(pairs) - 1, -1, -1):
                    if pairs[i]["tgt"] is not None:
                        pairs[i]["tgt"] = None
                        state["next"] = (i, "tgt")
                        break
            else:
                pi, side = nxt
                if side == "tgt" and pairs[pi]["cat"] is not None:
                    pairs[pi]["cat"] = None
                    state["next"] = (pi, "cat")
                elif pi > 0:
                    prev = pi - 1
                    pairs[prev]["tgt"] = None
                    state["next"] = (prev, "tgt")
            _update_status()
            _draw()

        def _reset_dlg():
            pairs.clear()
            for _ in range(MIN_PAIRS):
                pairs.append({"cat": None, "tgt": None})
            state["next"] = (0, "cat")
            _update_status()
            _draw()

        def _compute():
            try:
                _compute_inner()
            except Exception as exc:
                import traceback
                _set_status(f"⚠  Unexpected error: {exc}", "#FF6666")
                traceback.print_exc()
                _reset_dlg()

        def _compute_inner():
            if any(p["cat"] is None or p["tgt"] is None for p in pairs):
                _set_status(f"⚠  Missing points — complete all {len(pairs)} pairs first",
                            "#FF6666")
                _reset_dlg()
                return

            cat_img_pts = [p["cat"][2:4] for p in pairs]
            tgt_img_pts = [p["tgt"][2:4] for p in pairs]

            if _points_collinear([p["cat"] for p in pairs]):
                _set_status(
                    "⚠  Coin A points are collinear — pick non-collinear features",
                    "#FF6666")
                _reset_dlg(); return
            if _points_collinear([p["tgt"] for p in pairs]):
                _set_status(
                    "⚠  Coin B points are collinear — pick non-collinear features",
                    "#FF6666")
                _reset_dlg(); return

            src = np.float32(tgt_img_pts)
            dst = np.float32(cat_img_pts)

            M, _ = cv2.estimateAffinePartial2D(src, dst, method=cv2.LMEDS)

            if M is None:
                try:
                    M_full    = cv2.getAffineTransform(src[:3], dst[:3])
                    sx        = float(np.sqrt(M_full[0,0]**2 + M_full[1,0]**2))
                    sy        = float(np.sqrt(M_full[0,1]**2 + M_full[1,1]**2))
                    s_img     = (sx + sy) / 2
                    angle_deg = float(np.degrees(np.arctan2(M_full[1,0], M_full[0,0])))
                    M = M_full
                except Exception:
                    _set_status(
                        "⚠  Could not compute transform — try more spread-out points",
                        "#FF6666")
                    _reset_dlg(); return
            else:
                s_img     = float(np.sqrt(M[0,0]**2 + M[1,0]**2))
                angle_deg = float(np.degrees(np.arctan2(M[1,0], M[0,0])))

            if not (0.05 < s_img < 20):
                _set_status(
                    f"⚠  Scale result ({s_img:.3f}×) unreasonable — "
                    "pick more spread-out, clearly matching points", "#FF6666")
                _reset_dlg(); return
            if abs(angle_deg) > 175:
                _set_status(f"⚠  Rotation result ({angle_deg:.1f}°) unreasonable",
                            "#FF6666")
                _reset_dlg(); return

            cat_scale     = CANVAS_SIZE / (self._coin_a_img.width * self._coin_a_img.height) ** 0.5
            new_scale     = s_img * cat_scale
            tgt_cx        = self._target_rgba.width  / 2
            tgt_cy        = self._target_rgba.height / 2
            cat_cx_img    = M[0,0]*tgt_cx + M[0,1]*tgt_cy + M[0,2]
            cat_cy_img    = M[1,0]*tgt_cx + M[1,1]*tgt_cy + M[1,2]
            cat_cx_canvas = cat_cx_img * CANVAS_SIZE / self._coin_a_img.width
            cat_cy_canvas = cat_cy_img * CANVAS_SIZE / self._coin_a_img.height
            new_x_off     = int(cat_cx_canvas - CANVAS_SIZE / 2)
            new_y_off     = int(cat_cy_canvas - CANVAS_SIZE / 2)

            self.scale_var.set(round(new_scale, 4))
            self.rotation_var.set(round(angle_deg, 2))
            self.x_off_var.set(new_x_off)
            self.y_off_var.set(new_y_off)
            self._redraw()
            dlg.destroy()

        _update_status()
        _draw()


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Overlay two coin photographs to compare dies")
    parser.add_argument("--coin-a", default=None,
                        help="Path to coin A (base / reference image)")
    parser.add_argument("--coin-b", default=None,
                        help="Path to coin B (overlay image)")
    args = parser.parse_args()

    root = tk.Tk()
    app  = OverlayApp(root, args.coin_a, args.coin_b)
    root.mainloop()


if __name__ == "__main__":
    main()
