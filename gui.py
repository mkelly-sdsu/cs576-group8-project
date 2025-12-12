import tkinter as tk
from tkinter import messagebox, ttk
import math
import itertools
import threading
import time
import random

from tower import Tower
from ue import UE

# ----------------------------------------------------------------------
# GLOBAL simulation lists (shared by GUI and simulation thread)
# ----------------------------------------------------------------------
GLOBAL_TOWERS = []  # list of Tower sim objects
GLOBAL_UES = []     # list of UE sim objects


# Simple "env" object to mimic your SimPy env.now usage
class DummyEnv:
    def __init__(self, now: float = 0.0):
        self.now = now

def int_to_ip(x):
    return f"{(x >> 24) & 0xFF}.{(x >> 16) & 0xFF}.{(x >> 8) & 0xFF}.{x & 0xFF}"

class NetworkSimulationApp:
    def __init__(self, root: tk.Tk):
        self.HEX_SIZE = 60
        # Each hex radius = 300 meters → 300 / 60 = 5 m per pixel
        self.METERS_PER_PIXEL = 300 / self.HEX_SIZE   # = 5 meters per pixel
        self.root = root
        self.root.title("5G Network Simulation")
        self.root.configure(bg="#e0e0e0")
        self.root.geometry("1200x950")
        self.root.resizable(False, False)

        # --- Canvas & grid configuration ---
        self.WIDTH = 950
        self.HEIGHT = 700
        self.HEX_SIZE = 60
        self.BG_COLOR = "#ffffff"
        self.UI_COLOR = "#f5f5f5"

        self.horiz_spacing = math.sqrt(3) * self.HEX_SIZE
        self.vert_spacing = 1.5 * self.HEX_SIZE

        self.GRID_ROWS = 5
        self.GRID_COLS = 7
        self.MAX_GRID_RADIUS_PX = 2.5 * self.horiz_spacing

        # Colors
        self.COLOR_DISABLED = "#f0f0f0"
        self.COLOR_ACTIVE = "#2ecc71"
        self.COLOR_WARNING = "#f1c40f"   # used automatically at >= 50% utilization
        self.COLOR_OUTAGE = "#e74c3c"
        self.HEX_OUTLINE = "#000000"

        # State
        self.towers = {}              # hex_id -> tower dict
        self.tower_locations = set()  # (row, col)
        self.user_equipment = []      # list of UE dicts

        self.ip_counter = itertools.count(start=0x0A000000)   # unified IPv4 counter

        self.active_towers_list = []  # Tower sim objects
        self.active_ues_list = []     # UE sim objects

        self.t_delta = 0.5
        self.env = DummyEnv(now=0.0)

        self.placement_mode = None
        self._drag_data = {"item": None, "x": 0, "y": 0, "dragging": False}

        self.sim_thread = None
        self.sim_running = False

        # Outage bookkeeping
        self._outage_remaining = 0
        self._outage_prev_status = {}

        # Tower link bookkeeping
        self._tower_link_ids = []

        # Create noise drop-down
        self.simulate_noise_var = tk.StringVar(value="False")

        # Number of steps per second
        self.steps_per_sec_var = tk.StringVar(value="1")   # default: 1 step per second

        # Build UI
        self._setup_ui()

        # Make Tk produce REAL canvas size
        self.root.update_idletasks()
        self.WIDTH  = self.canvas.winfo_width()
        self.HEIGHT = self.canvas.winfo_height()

        self.start_x, self.start_y, self.grid_center_x, self.grid_center_y = self._calculate_grid_start()
        self._create_grid()

        # Randomly start with 5 active towers in a tree topology
        self._initialize_random_towers(num=5)

        # Update UE tower lists right after random towers are active
        for ue_data in self.user_equipment:
            ue = ue_data["sim_object"]
            ue.update_towers(list(GLOBAL_TOWERS))

        self.add_user_equipment(100, 100)

        # And update towers for new UE as well:
        for ue_data in self.user_equipment:
            ue = ue_data["sim_object"]
            ue.t_step = 0
            ue.update_towers(list(GLOBAL_TOWERS))

        self.BAND_VISUAL_RADII = {
            "high":  300,   # current value
            "mid" : 1500,   # twice as big
            "low" : 5000,   # much larger visualization
        }


    # ------------------------------------------------------------------
    # UI setup
    # ------------------------------------------------------------------
    def _calculate_grid_start(self):
        grid_width = (self.GRID_COLS * self.horiz_spacing) + (self.horiz_spacing / 2)
        grid_height = (self.GRID_ROWS * self.vert_spacing) + (self.HEX_SIZE / 2)

        start_x = self.WIDTH / 2 - grid_width / 2 + (self.horiz_spacing / 2)
        start_y = self.HEIGHT / 2 - grid_height / 2 + self.HEX_SIZE

        grid_center_x = self.WIDTH / 2
        grid_center_y = self.HEIGHT / 2

        return start_x, start_y, grid_center_x, grid_center_y

    def _setup_ui(self):
        main_frame = tk.Frame(self.root, bg=self.root["bg"])
        main_frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=15)

        tk.Label(
            main_frame,
            text="5G Network Simulator",
            font=("Arial", 18, "bold"),
            bg=main_frame["bg"],
        ).pack(pady=(0, 10))

        # Control area – two stacked rows
        control_frame = tk.Frame(main_frame, bg=self.UI_COLOR, padx=10, pady=10)
        control_frame.pack(fill=tk.X, pady=5)

        top_row = tk.Frame(control_frame, bg=self.UI_COLOR)
        top_row.pack(fill=tk.X)

        bottom_row = tk.Frame(control_frame, bg=self.UI_COLOR)
        bottom_row.pack(fill=tk.X, pady=(8, 0))

        # ---- Top row: placement + start/stop ----
        tk.Label(
            top_row,
            text="Select Object to Place:",
            font=("Arial", 10),
            bg=self.UI_COLOR,
        ).pack(side=tk.LEFT, padx=5)

        btn_style = {
            "font": ("Arial", 10, "bold"),
            "fg": "white",
            "relief": tk.FLAT,
            "padx": 15,
            "pady": 5,
            "width": 15,
        }

        self.place_grid_tower_button = tk.Button(
            top_row,
            text="Place New TOWER",
            command=self.set_grid_tower_placement_mode,
            bg="#3498db",
            activebackground="#2980b9",
            **btn_style,
        )
        self.place_grid_tower_button.pack(side=tk.LEFT, padx=5)

        self.place_phone_button = tk.Button(
            top_row,
            text="Place PHONE (UE)",
            command=self.set_phone_placement_mode,
            bg="#2ecc71",
            activebackground="#27ae60",
            **btn_style,
        )
        self.place_phone_button.pack(side=tk.LEFT, padx=5)

        self.start_sim_button = tk.Button(
            top_row,
            text="Start SIM",
            command=self.start_simulation,
            bg="#27ae60",
            fg="white",
            relief=tk.FLAT,
            width=12,
        )
        self.start_sim_button.pack(side=tk.LEFT, padx=5)

        self.stop_sim_button = tk.Button(
            top_row,
            text="Stop SIM",
            command=self.stop_simulation,
            bg="#c0392b",
            fg="white",
            relief=tk.FLAT,
            width=12,
        )
        self.stop_sim_button.pack(side=tk.LEFT, padx=5)

        # ---- Bottom row: outage, reset UEs, current action ----
        tk.Label(
            bottom_row,
            text="Outage Steps:",
            bg=self.UI_COLOR,
        ).pack(side=tk.LEFT, padx=(5, 5))
        self.outage_steps_var = tk.StringVar(value="5")
        outage_entry = tk.Entry(bottom_row, textvariable=self.outage_steps_var, width=6)
        outage_entry.pack(side=tk.LEFT)

        self.outage_button = tk.Button(
            bottom_row,
            text="Simulate Total Outage",
            bg="#e74c3c",
            fg="white",
            relief=tk.FLAT,
            command=self.trigger_total_outage,
        )
        self.outage_button.pack(side=tk.LEFT, padx=10)

        self.disable_all_button = tk.Button(
            bottom_row,
            text="Disable All Towers",
            bg="#8e44ad",
            fg="white",
            relief=tk.FLAT,
            command=self.disable_all_towers
        )
        self.disable_all_button.pack(side=tk.LEFT, padx=10)

        self.reset_ues_button = tk.Button(
            bottom_row,
            text="Reset UEs",
            bg="#7f8c8d",
            fg="white",
            relief=tk.FLAT,
            command=self.reset_all_ues_tx,
        )
        self.reset_ues_button.pack(side=tk.LEFT, padx=5)

        self.action_var = tk.StringVar(value="Current Action: None")
        tk.Label(
            bottom_row,
            textvariable=self.action_var,
            font=("Arial", 10, "italic"),
            bg=self.UI_COLOR,
        ).pack(side=tk.LEFT, padx=20)

        # Simulate Noise dropdown
        tk.Label(
            bottom_row, text="Simulate Noise:", bg=self.UI_COLOR
        ).pack(side=tk.LEFT, padx=(20, 5))

        simulate_noise_box = ttk.Combobox(
            bottom_row,
            textvariable=self.simulate_noise_var,
            values=["True", "False"],
            state="readonly",
            width=7
        )
        simulate_noise_box.pack(side=tk.LEFT)
        simulate_noise_box.current(1)   # default "False"

        # ----------------------------
        # N simulation steps per second
        # ----------------------------
        tk.Label(
            top_row, text="N steps/sec:", bg=self.UI_COLOR
        ).pack(side=tk.LEFT, padx=(15, 5))

        self.steps_per_sec_entry = tk.Entry(
            top_row, textvariable=self.steps_per_sec_var, width=6
        )
        self.steps_per_sec_entry.pack(side=tk.LEFT)

        # Canvas
        self.canvas = tk.Canvas(
            main_frame,
            width=self.WIDTH,
            height=self.HEIGHT,
            bg=self.BG_COLOR,
            highlightthickness=0,
            borderwidth=0,
        )
        self.canvas.pack(pady=10, fill=tk.BOTH, expand=True)
        self.root.update_idletasks()  # Make Tk compute final sizes
        self.WIDTH  = self.canvas.winfo_width()
        self.HEIGHT = self.canvas.winfo_height()

        # Visualization band selection
        tk.Label(
            bottom_row, text="Visual Band:", bg=self.UI_COLOR
        ).pack(side=tk.LEFT, padx=(15, 5))

        self.visual_band_var = tk.StringVar(value="high")
        visual_band_box = ttk.Combobox(
            bottom_row,
            textvariable=self.visual_band_var,
            values=["low", "mid", "high"],
            state="readonly",
            width=7
        )
        visual_band_box.pack(side=tk.LEFT)
        visual_band_box.current(2)  # default "high"

        visual_band_box.bind("<<ComboboxSelected>>", lambda e: self.on_band_change())


        # Status bar
        self.status_var = tk.StringVar(value="Ready. Select an object type to begin placing.")
        tk.Label(
            self.root,
            textvariable=self.status_var,
            bd=1,
            relief=tk.FLAT,
            anchor=tk.W,
            bg="#cccccc",
            fg="#333333",
            font=("Arial", 9),
        ).pack(side=tk.BOTTOM, fill=tk.X)

        # Canvas events
        self.canvas.bind("<Button-1>", self.on_canvas_click)
        self.canvas.tag_bind("user_equipment_body", "<ButtonPress-1>", self.on_user_press)
        self.canvas.tag_bind("user_equipment_body", "<ButtonRelease-1>", self.on_user_release)
        self.canvas.tag_bind("user_equipment_body", "<B1-Motion>", self.on_user_drag)

    # ------------------------------------------------------------------
    # UI mode helpers
    # ------------------------------------------------------------------
    def reset_ui_mode(self):
        self.placement_mode = None
        self.canvas.config(cursor="arrow")
        self.action_var.set("Current Action: None")
        self.place_grid_tower_button.config(bg="#3498db")
        self.place_phone_button.config(bg="#2ecc71")
        self.status_var.set("Ready. Select an object type to begin placing.")

    def set_grid_tower_placement_mode(self):
        if self.placement_mode == "GRID_TOWER":
            self.reset_ui_mode()
            return
        self.placement_mode = "GRID_TOWER"
        self.canvas.config(cursor="tcross")
        self.action_var.set("Current Action: Placing GRID TOWER")
        self.place_grid_tower_button.config(bg="#c0392b")
        self.place_phone_button.config(bg="#2ecc71")
        self.status_var.set("MODE: Placing TOWER. Left-Click to snap and drop a new Tower.")

    def set_phone_placement_mode(self):
        if self.placement_mode == "PHONE":
            self.reset_ui_mode()
            return
        self.placement_mode = "PHONE"
        self.canvas.config(cursor="crosshair")
        self.action_var.set("Current Action: Placing PHONE")
        self.place_grid_tower_button.config(bg="#3498db")
        self.place_phone_button.config(bg="#c0392b")
        self.status_var.set("MODE: Placing PHONE. Left-Click to drop a new User Equipment.")

    # ------------------------------------------------------------------
    # Hex grid & towers
    # ------------------------------------------------------------------
    @staticmethod
    def _hex_corners(cx, cy, size):
        pts = []
        for i in range(6):
            ang = math.radians(60 * i - 30)
            pts.append((cx + size * math.cos(ang), cy + size * math.sin(ang)))
        return pts

    def _draw_hexagon(self, x, y, row, col):
        """
        Draws a single tower hex, always showing:
        - Hex outline
        - Carrot icon ^
        - Tower IP
        No telemetry here (as requested).
        """
        tower_ip = next(self.ip_counter)
        tower_id = int_to_ip(tower_ip)

        tower_sim = Tower(
            # env=self.env,
            # tower_id=f"GridTower_R{row}_C{col}",
            tower_id=tower_id,
            x_pos=x * self.METERS_PER_PIXEL,
            y_pos=y * self.METERS_PER_PIXEL,
            t_delta=self.t_delta,
            ip_addr=tower_ip,
        )

        corners = self._hex_corners(x, y, self.HEX_SIZE)
        hex_id = self.canvas.create_polygon(
            corners,
            outline=self.HEX_OUTLINE,
            fill=self.COLOR_DISABLED,
            width=2,
            tags=("hexagon_cell", "tower")
        )

        # IP text in hex center
        ip_text_id = self.canvas.create_text(
            x, y,
            text=str(tower_id),
            font=("Arial", 12, "bold"),
            fill=self.HEX_OUTLINE,
            tags=("tower_ip_text",)
        )

        # Carrot above hex
        icon_id = self.canvas.create_text(
            x, y - 18,
            text="^",
            font=("Arial", 20, "bold"),
            fill=self.HEX_OUTLINE,
            tags=("tower_icon",)
        )

        self.towers[hex_id] = {
            "id": tower_id,
            "row": row,
            "col": col,
            "x": x,
            "y": y,
            "status": "DISABLED",
            "icon_id": icon_id,
            "ip_text_id": ip_text_id,
            "ip_addr": tower_ip,
            "sim_object": tower_sim,
        }

        self.tower_locations.add((row, col))

        # Keep hex at bottom, carrot & IP on top
        self.canvas.tag_lower(hex_id)
        self.canvas.tag_raise(icon_id)
        self.canvas.tag_raise(ip_text_id)

        # Bind tower click
        for obj in (hex_id, icon_id, ip_text_id):
            self.canvas.tag_bind(obj, "<Button-1>", lambda e, hid=hex_id: self.on_tower_click(hid))

    def _create_grid(self):
        count = 0
        for r in range(self.GRID_ROWS):
            offset_x = self.horiz_spacing / 2 if r % 2 == 1 else 0
            for c in range(self.GRID_COLS):
                x = self.start_x + c * self.horiz_spacing + offset_x
                y = self.start_y + r * self.vert_spacing
                if (x - self.grid_center_x) ** 2 + (y - self.grid_center_y) ** 2 < self.MAX_GRID_RADIUS_PX ** 2:
                    self._draw_hexagon(x, y, r, c)
                    count += 1
        print("Towers Created:", count)

    def _snap_to_grid(self, x, y):
        r_float = (y - self.start_y) / self.vert_spacing
        r_int = round(r_float)
        offset_x = self.horiz_spacing / 2 if r_int % 2 == 1 else 0

        xx = x - offset_x
        c_float = (xx - self.start_x) / self.horiz_spacing
        c_int = round(c_float)

        sx = self.start_x + c_int * self.horiz_spacing + offset_x
        sy = self.start_y + r_int * self.vert_spacing

        if math.hypot(x - sx, y - sy) > self.HEX_SIZE:
            return None, None, None, None

        return r_int, c_int, sx, sy

    def on_canvas_click(self, event):
        """
        Handles grid snapping for towers OR dropping new UEs.
        """
        mode = self.placement_mode

        # Clicking towers/UEs handled elsewhere
        if mode is None:
            closest = self.canvas.find_closest(event.x, event.y)[0]
            tags = self.canvas.gettags(closest)
            if any(t in tags for t in ("user_equipment_body", "tower", "tower_icon", "tower_ip_text")):
                return

        if mode == "GRID_TOWER":
            r, c, sx, sy = self._snap_to_grid(event.x, event.y)
            if sx is None:
                self.status_var.set("Too far from grid snap.")
            else:
                if 0 <= sx <= self.WIDTH and 0 <= sy <= self.HEIGHT:
                    if (r, c) not in self.tower_locations:
                        self._draw_hexagon(sx, sy, r, c)
                        self.status_var.set(f"Tower placed at Grid ({r},{c})")
                    else:
                        self.status_var.set("Tower already exists here.")
                else:
                    self.status_var.set("Cannot place tower outside canvas.")

        elif mode == "PHONE":
            cx = max(20, min(self.WIDTH - 20, event.x))
            cy = max(40, min(self.HEIGHT - 40, event.y))
            self.add_user_equipment(cx, cy)
            self.status_var.set(f"UE placed at ({cx},{cy})")

        if mode:
            self.reset_ui_mode()

    # ------------------------------------------------------------------
    # Random tower activation & topology
    # ------------------------------------------------------------------
    def _initialize_random_towers(self, num=5):
        """
        Randomly activates `num` towers at startup and connects them
        in a fixed tree topology (0-1, 0-2, 2-3, 3-4).
        """
        hex_ids = list(self.towers.keys())
        selected = random.sample(hex_ids, min(num, len(hex_ids)))

        # Activate only these towers
        for h in selected:
            self.set_tower_status(h, "ACTIVE")

        selected_sims = [self.towers[h]["sim_object"] for h in selected]

        # Tree connections
        links = [(0, 1), (0, 2), (2, 3), (3, 4)]
        for a, b in links:
            if a < len(selected_sims) and b < len(selected_sims):
                selected_sims[a].connect_tower(selected_sims[b])

        # Set tx_attempts
        for t in GLOBAL_TOWERS:
            t.tx_attempts = len(GLOBAL_TOWERS)+1

    # ------------------------------------------------------------------
    # UEs: create, drag, delete
    # ------------------------------------------------------------------
    def add_user_equipment(self, x, y):
        """
        Draws a UE (rectangle + antenna) and sets up its sim object.
        """
        ue_ip = next(self.ip_counter)
        ue_id = int_to_ip(ue_ip)

        ue_sim = UE(
            # env=self.env,
            # ue_id=len(self.user_equipment),
            ue_id=ue_id,
            x_pos=x * self.METERS_PER_PIXEL,
            y_pos=y * self.METERS_PER_PIXEL,
            towers=self.active_towers_list,
            t_delta=self.t_delta,
            ip_addr=ue_ip
        )

        ue_sim.tx_target_ip = None
        ue_sim.tx_mode = "fixed"   # fixed, random, max
        ue_sim.tx_n_bytes = 512
        ue_sim.gui_last_n_tx_bytes = 0

        self.active_ues_list.append(ue_sim)
        GLOBAL_UES.append(ue_sim)

        # Draw UE icon
        width, height = 15, 27
        body = self.canvas.create_rectangle(
            x - width / 2, y - height / 2,
            x + width / 2, y + height / 2,
            fill="#34495e", outline="gray", width=1,
            tags=("user_equipment_body",)
        )

        ant = self.canvas.create_line(
            x, y - height / 2,
            x, y - height / 2 - 8,
            fill="#7f8c8d", width=2,
            tags=("user_equipment_part", f"part_{body}")
        )
        dot = self.canvas.create_oval(
            x - 2, y - height / 2 - 10,
            x + 2, y - height / 2 - 6,
            fill="#7f8c8d",
            tags=("user_equipment_part", f"part_{body}")
        )
        scr = self.canvas.create_rectangle(
            x - 5, y - height / 2 + 5,
            x + 5, y - height / 2 + 13,
            fill="#27ae60", outline="white",
            tags=("user_equipment_part", f"part_{body}")
        )

        # UE label
        max_mbps = (ue_sim.max_data_rate * getattr(ue_sim, "code_rate", 1)) * 1e-6
        label = self.canvas.create_text(
            x, y + 30,
            text=f"IP: {ue_id} | Max: {max_mbps:.1f} Mbps | CR: N/A\nActual: 0.000 Mbps | BER: 0.0",
            font=("Arial", 9), fill="black",
            tags=("ue_label", f"ue_label_{body}")
        )

        self.user_equipment.append({
            "id": body,
            "parts": [ant, dot, scr],
            "x": x, "y": y,                  # canvas pixels
            "ip_addr": ue_ip,
            "sim_object": ue_sim,
            "conn_line_id": None,
            "label_id": label,
        })

        self.canvas.tag_raise(body)
        self.canvas.tag_raise(f"part_{body}")
        self.canvas.tag_raise(label)

    def on_user_press(self, event):
        """
        Begin dragging a UE.
        """
        item = self.canvas.find_closest(event.x, event.y)[0]
        if "user_equipment_body" not in self.canvas.gettags(item):
            self._drag_data["item"] = None
            return

        self._drag_data["item"] = item
        self._drag_data["x"] = event.x
        self._drag_data["y"] = event.y
        self._drag_data["dragging"] = False

        # Bring UE layers to top
        self.canvas.tag_raise(item)
        self.canvas.tag_raise(f"part_{item}")
        self.canvas.tag_raise("ue_label")

    def on_user_drag(self, event):
        """
        Drag a UE and update its position + green link live.
        """
        item = self._drag_data["item"]
        if item is None:
            return

        # Current center of UE
        bbox = self.canvas.bbox(item)
        cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2

        # New center (clamped to window)
        new_x = cx + (event.x - self._drag_data["x"])
        new_y = cy + (event.y - self._drag_data["y"])
        new_x = max(20, min(self.WIDTH - 20, new_x))
        new_y = max(40, min(self.HEIGHT - 40, new_y))

        dx, dy = new_x - cx, new_y - cy
        if abs(dx) > 0 or abs(dy) > 0:
            self._drag_data["dragging"] = True

        # Move UE body + antenna parts
        self.canvas.move(item, dx, dy)
        self.canvas.move(f"part_{item}", dx, dy)

        # Update drag reference
        self._drag_data["x"] = event.x
        self._drag_data["y"] = event.y

        # Update sim + label
        ue_data = next((u for u in self.user_equipment if u["id"] == item), None)
        if ue_data:
            ue = ue_data["sim_object"]

            # Canvas positions in pixels
            ue_data["x"], ue_data["y"] = new_x, new_y
            # Sim positions in meters
            ue.x_pos, ue.y_pos = new_x * self.METERS_PER_PIXEL, new_y * self.METERS_PER_PIXEL

            self.canvas.coords(ue_data["label_id"], new_x, new_y + 30)

            # Update link line live
            self.update_ue_connection_line(ue_data)

        self.status_var.set(f"Dragging UE… ({int(new_x)}, {int(new_y)})")


    def on_user_release(self, event):
        """
        Finalize drag, or open popup on click.
        """
        item = self._drag_data["item"]
        if item is None:
            return

        ue_data = next((u for u in self.user_equipment if u["id"] == item), None)
        if ue_data:
            bbox = self.canvas.bbox(item)
            cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2

            # Canvas pixels
            ue_data["x"], ue_data["y"] = cx, cy
            ue = ue_data["sim_object"]
            # Sim meters
            ue.x_pos, ue.y_pos = cx * self.METERS_PER_PIXEL, cy * self.METERS_PER_PIXEL

            self.canvas.coords(ue_data["label_id"], cx, cy + 30)
            self.update_ue_connection_line(ue_data)

            if not self._drag_data["dragging"]:
                self.show_ue_popup_by_id(item)

            self.status_var.set(f"UE moved to ({int(cx)}, {int(cy)})")

        self._drag_data["item"] = None
        self._drag_data["dragging"] = False



    # ----------------------------------------------------------------------
    # UE-Tower Connection Line (Green)
    # ----------------------------------------------------------------------
    def update_ue_connection_line(self, ue_data):
        """
        Draws/updates the green UE→Tower line.
        """
        old = ue_data.get("conn_line_id")
        if old:
            self.canvas.delete(old)

        ue = ue_data["sim_object"]
        tower = getattr(ue, "current_tower", None)
        if tower is None:
            ue_data["conn_line_id"] = None
            return

        # Tower coordinates
        tx = ty = None
        for hex_id, t in self.towers.items():
            if t["sim_object"] is tower:
                tx, ty = t["x"], t["y"]
                break

        if tx is None:
            ue_data["conn_line_id"] = None
            return

        x, y = ue_data["x"], ue_data["y"]
        line = self.canvas.create_line(
            x, y, tx, ty,
            # fill=self.COLOR_ACTIVE,
            fill="#3498db",
            width=2,
            tags=("ue_connection_line",)
        )

        # Correct visual stacking
        self.canvas.tag_lower("hexagon_cell")
        self.canvas.tag_raise("tower_icon")
        self.canvas.tag_raise("tower_ip_text")
        self.canvas.tag_raise(line)
        self.canvas.tag_raise("user_equipment_body")
        self.canvas.tag_raise("user_equipment_part")
        self.canvas.tag_raise("ue_label")

        ue_data["conn_line_id"] = line

    def refresh_all_connection_lines(self):
        for ue_data in self.user_equipment:
            self.update_ue_connection_line(ue_data)

    # ----------------------------------------------------------------------
    # Tower-to-Tower Links (Blue Lines)
    # ----------------------------------------------------------------------
    def draw_tower_links(self):
        """
        Always draw ALL tower-to-tower backhaul links.
        Dotted blue lines remain visible at all times.
        """
        # Clear previous lines
        for lid in self._tower_link_ids:
            self.canvas.delete(lid)
        self._tower_link_ids.clear()

        # Draw every existing tower connection
        seen = set()

        for hex_id, data in self.towers.items():
            sim = data["sim_object"]
            x1, y1 = data["x"], data["y"]

            for other in getattr(sim, "connected_towers", []):
                pair = tuple(sorted((id(sim), id(other))))
                if pair in seen:
                    continue
                seen.add(pair)

                # find GUI coords of the connected tower
                for hid2, d2 in self.towers.items():
                    if d2["sim_object"] is other:
                        x2, y2 = d2["x"], d2["y"]
                        break
                else:
                    continue

                line = self.canvas.create_line(
                    x1, y1, x2, y2,
                    fill="#3498db",
                    width=2,
                    dash=(4, 4),
                    tags=("tower_link",)
                )

                self._tower_link_ids.append(line)

            # Make them sit BELOW UE→tower lines but ABOVE the hex cells
            # 1) ensure links are above the hexagon cells
            self.canvas.tag_raise("tower_link", "hexagon_cell")

            # 2) if UE→tower connection lines exist, put links just below them
            if self.canvas.find_withtag("ue_connection_line"):
                self.canvas.tag_lower("tower_link", "ue_connection_line")




    # ----------------------------------------------------------------------
    # UE Popup + Transmit Config Window
    # ----------------------------------------------------------------------
    def show_ue_popup_by_id(self, body_id):
        ue_data = next((u for u in self.user_equipment if u["id"] == body_id), None)
        if ue_data:
            self._show_ue_popup(ue_data)

    def _show_ue_popup(self, ue_data):
        ue = ue_data["sim_object"]

        top = tk.Toplevel(self.root)
        top.title(f"Control: UE {int_to_ip(ue_data['ip_addr'])}")
        top.geometry("360x430")  # slightly taller
        top.configure(bg=self.UI_COLOR)

        tk.Label(top, text=f"UE IP: {int_to_ip(ue_data['ip_addr'])}",
                 font=("Arial", 12, "bold"), bg=self.UI_COLOR).pack(pady=5)

        tk.Label(top, text=f"Position: X={int(ue_data['x'])}, Y={int(ue_data['y'])}",
                 bg=self.UI_COLOR).pack()

        tower_ip = ue.current_tower.ip_addr if ue.current_tower else "None"
        band = ue.freq_band or "None"
        cr = ue.code_rate
        cr_str = f"{cr:.3f}" if isinstance(cr, (int, float)) else "N/A"

        tk.Label(top, text=f"Connected Tower: {tower_ip}", bg=self.UI_COLOR).pack()
        tk.Label(top, text=f"Band: {band}, Code Rate: {cr_str}", bg=self.UI_COLOR).pack()

        actual_mbps = ue.gui_last_n_tx_bytes * 8e-6 * int(self.steps_per_sec_var.get())
        effective_max = ue.max_data_rate * (ue.code_rate or 1)
        max_mbps = effective_max * 1e-6

        tk.Label(top, text=f"Actual Data Rate: {actual_mbps:.3f} Mbps", bg=self.UI_COLOR).pack()
        tk.Label(top, text=f"Max Data Rate:    {max_mbps:.3f} Mbps", bg=self.UI_COLOR).pack()

        # TX Menu
        tk.Button(
            top,
            text="Transmit Data",
            bg="#2980b9",
            fg="white",
            relief=tk.FLAT,
            command=lambda: self.open_transmit_window(ue),
        ).pack(pady=10, fill=tk.X, padx=20)

        # DELETE UE
        tk.Button(
            top,
            text="Delete UE",
            bg="#c0392b",
            fg="white",
            relief=tk.FLAT,
            command=lambda: self.delete_ue(ue_data["id"], top),
        ).pack(pady=12, fill=tk.X, padx=20)

        tk.Button(
            top,
            text="Close",
            bg="#bdc3c7",
            relief=tk.FLAT,
            command=top.destroy,
        ).pack(pady=5)

    def open_transmit_window(self, sender_ue: UE):
        """
        Configure persistent transmission for a UE:
        - Destination: NONE, UE, or BROADCAST (65535)
        - Mode: Fixed, Random, or Max (uses max_data_rate * code_rate)
        """
        win = tk.Toplevel(self.root)
        win.title(f"Transmit from UE {int_to_ip(sender_ue.ip_addr)}")
        win.geometry("380x420")     # Taller so nothing is truncated
        win.configure(bg=self.UI_COLOR)

        tk.Label(
            win,
            text=f"Sender UE IP: {int_to_ip(sender_ue.ip_addr)}",
            font=("Arial", 12, "bold"),
            bg=self.UI_COLOR,
        ).pack(pady=5)

        # -----------------------------------
        # DESTINATION DROPDOWN
        # -----------------------------------
        tk.Label(win, text="Destination:", bg=self.UI_COLOR).pack(pady=(10, 2))

        dest_labels = []
        label_to_ip = {}

        # Build from GUI UE list to avoid duplicates
        for ue_data in self.user_equipment:
            ue_sim = ue_data["sim_object"]
            rid = ue_data["id"]
            if ue_sim is sender_ue:
                lbl = f"UE {rid} (IP: {int_to_ip(ue_sim.ip_addr)})  [self]"
            else:
                lbl = f"UE {rid} (IP: {int_to_ip(ue_sim.ip_addr)})"
            dest_labels.append(lbl)
            label_to_ip[lbl] = ue_sim.ip_addr

        # Broadcast + NONE
        broadcast_ip = 65535
        broadcast_label = f"BROADCAST ({broadcast_ip})"
        dest_labels.append(broadcast_label)
        label_to_ip[broadcast_label] = broadcast_ip

        none_label = "NONE (no transmission)"
        dest_labels.insert(0, none_label)
        label_to_ip[none_label] = None

        # Default selected destination
        cur_target = sender_ue.tx_target_ip
        default_label = none_label
        if cur_target is not None:
            for lbl, ip in label_to_ip.items():
                if ip == cur_target:
                    default_label = lbl
                    break

        dest_var = tk.StringVar(value=default_label)
        dest_box = ttk.Combobox(
            win, textvariable=dest_var, values=dest_labels, state="readonly"
        )
        dest_box.pack(fill=tk.X, padx=20, pady=5)

        # -----------------------------------
        # TRANSMIT MODE
        # -----------------------------------
        tk.Label(win, text="Transmit Mode:", bg=self.UI_COLOR).pack(pady=(10, 2))
        mode_frame = tk.Frame(win, bg=self.UI_COLOR)
        mode_frame.pack(pady=2)

        mode_var = tk.StringVar(value=sender_ue.tx_mode)

        tk.Radiobutton(
            mode_frame,
            text="Fixed (use Number of bytes)",
            variable=mode_var,
            value="fixed",
            bg=self.UI_COLOR,
        ).pack(anchor="w")

        tk.Radiobutton(
            mode_frame,
            text="Random (1–65,535 bytes each timestep)",
            variable=mode_var,
            value="random",
            bg=self.UI_COLOR,
        ).pack(anchor="w")

        tk.Radiobutton(
            mode_frame,
            text="Max (use max_data_rate * code_rate)",
            variable=mode_var,
            value="max",
            bg=self.UI_COLOR,
        ).pack(anchor="w")

        # -----------------------------------
        # FIXED BYTE ENTRY
        # -----------------------------------
        tk.Label(win, text="Number of Bytes (Fixed Mode):", bg=self.UI_COLOR).pack(pady=(10, 2))
        byte_entry = tk.Entry(win)
        byte_entry.insert(0, str(sender_ue.tx_n_bytes))
        byte_entry.pack(fill=tk.X, padx=20, pady=5)

        # -----------------------------------
        # APPLY BUTTON (no Close button)
        # -----------------------------------
        def apply_settings():
            label = dest_var.get()
            dest_ip = label_to_ip[label]
            mode = mode_var.get()

            # Fixed mode
            txt = byte_entry.get().strip()
            if txt == "":
                nbytes = 0
            else:
                try:
                    nbytes = int(txt)
                    if nbytes < 0:
                        raise ValueError
                except ValueError:
                    messagebox.showwarning(
                        "Invalid Bytes", "Please enter a non-negative integer."
                    )
                    return

            sender_ue.tx_target_ip = dest_ip
            sender_ue.tx_mode = mode
            sender_ue.tx_n_bytes = nbytes

            if dest_ip is None:
                self.status_var.set(f"UE {int_to_ip(sender_ue.ip_addr)}: TX disabled.")
            else:
                msg = {
                    "fixed": f"Fixed {nbytes} bytes",
                    "random": "Random bytes",
                    "max": "Max rate mode",
                }[mode]
                self.status_var.set(f"UE {int_to_ip(sender_ue.ip_addr)}: {msg} → IP {int_to_ip(dest_ip)}")

            win.destroy()

        tk.Button(
            win,
            text="Set",
            bg="#27ae60",
            fg="white",
            relief=tk.FLAT,
            command=apply_settings,
        ).pack(pady=12, fill=tk.X, padx=20)

    # Change the distance based on band representation
    def on_band_change(self, *args):
        band = self.visual_band_var.get()

        # update pixel → meter scaling
        self.METERS_PER_PIXEL = self.BAND_VISUAL_RADII[band] / self.HEX_SIZE

        # update all existing towers and UEs with new scaling
        for hex_id, data in self.towers.items():
            sim = data["sim_object"]
            sim.x_pos = data["x"] * self.METERS_PER_PIXEL
            sim.y_pos = data["y"] * self.METERS_PER_PIXEL

        for ue_data in self.user_equipment:
            ue = ue_data["sim_object"]
            ue.x_pos = ue_data["x"] * self.METERS_PER_PIXEL
            ue.y_pos = ue_data["y"] * self.METERS_PER_PIXEL

        self.status_var.set(f"Band changed to {band.upper()} – coverage updated.")

        self.draw_tower_links()

    # ----------------------------------------------------------------------
    # DELETE UE
    # ----------------------------------------------------------------------
    def delete_ue(self, ue_id, top_window):
        # Find UE index
        try:
            idx = next(i for i, d in enumerate(self.user_equipment) if d["id"] == ue_id)
        except StopIteration:
            top_window.destroy()
            return

        ue_data = self.user_equipment.pop(idx)

        # Remove line
        if ue_data.get("conn_line_id"):
            self.canvas.delete(ue_data["conn_line_id"])

        # Remove UE graphics
        self.canvas.delete(ue_id)
        for pid in ue_data["parts"]:
            self.canvas.delete(pid)
        self.canvas.delete(ue_data["label_id"])

        sim_obj = ue_data["sim_object"]
        if sim_obj in self.active_ues_list:
            self.active_ues_list.remove(sim_obj)
        if sim_obj in GLOBAL_UES:
            GLOBAL_UES.remove(sim_obj)

        self.status_var.set(f"UE (IP {ue_data['ip_addr']}) deleted.")
        top_window.destroy()

    # ----------------------------------------------------------------------
    # TOWERS: status, connect, disconnect, delete
    # ----------------------------------------------------------------------
    def activate_tower(self, hex_id):
        data = self.towers.get(hex_id)
        if not data:
            return

        sim = data["sim_object"]
        sim.operational = True

        # Add tower to global / active lists
        if sim not in GLOBAL_TOWERS:
            GLOBAL_TOWERS.append(sim)
        if sim not in self.active_towers_list:
            self.active_towers_list.append(sim)

        for ue_data in self.user_equipment:
            ue = ue_data["sim_object"]
            ue.update_towers(list(GLOBAL_TOWERS))
            self.update_ue_connection_line(ue_data)


        # GUI color
        self.canvas.itemconfig(hex_id, fill=self.COLOR_ACTIVE)
        data["status"] = "ACTIVE"

        # UEs attach in simulation loop only
        self.refresh_all_connection_lines()
        self.draw_tower_links()

    def deactivate_tower(self, hex_id):
        data = self.towers.get(hex_id)
        if not data:
            return

        sim = data["sim_object"]
        sim.operational = False

        if sim in GLOBAL_TOWERS:
            GLOBAL_TOWERS.remove(sim)
        if sim in self.active_towers_list:
            self.active_towers_list.remove(sim)

        # GUI color
        self.canvas.itemconfig(hex_id, fill=self.COLOR_DISABLED)
        data["status"] = "DISABLED"

        # Detach UEs and remove green lines
        for ue_data in self.user_equipment:
            ue = ue_data["sim_object"]
            ue.update_towers(list(GLOBAL_TOWERS))

            if ue.current_tower is None and ue_data.get("conn_line_id"):
                self.canvas.delete(ue_data["conn_line_id"])
                ue_data["conn_line_id"] = None

        self.draw_tower_links()

    def delete_tower(self, hex_id, top_window):
        data = self.towers.get(hex_id)
        if not data:
            top_window.destroy()
            return

        # fully disable first
        self.deactivate_tower(hex_id)

        # delete visuals
        self.canvas.delete(hex_id)
        self.canvas.delete(data["icon_id"])
        self.canvas.delete(data["ip_text_id"])

        sim = data["sim_object"]
        if sim in GLOBAL_TOWERS:
            GLOBAL_TOWERS.remove(sim)

        del self.towers[hex_id]
        self.tower_locations.discard((data["row"], data["col"]))

        self.draw_tower_links()

        self.status_var.set(f"{data['id']} deleted.")
        top_window.destroy()

    def set_tower_status(self, hex_id, status):
        """
        Handles ACTIVE / WARNING / OUTAGE / DISABLED logic and tower coloring.
        """
        data = self.towers.get(hex_id)
        if not data:
            return
        sim = data["sim_object"]

        # ----- Status transitions -----
        if status == "ACTIVE":
            color = self.COLOR_ACTIVE
            sim.operational = True
            if sim not in GLOBAL_TOWERS:
                GLOBAL_TOWERS.append(sim)
            if sim not in self.active_towers_list:
                self.active_towers_list.append(sim)

        elif status == "WARNING":
            # Yellow but still operational
            color = self.COLOR_WARNING
            sim.operational = True
            # if sim not in GLOBAL_TOWERS:
                # GLOBAL_TOWERS.append(sim)
            if sim not in self.active_towers_list:
                self.active_towers_list.append(sim)

        elif status == "OUTAGE":
            color = self.COLOR_OUTAGE
            sim.operational = False
            if sim in GLOBAL_TOWERS:
                GLOBAL_TOWERS.remove(sim)
            if sim in self.active_towers_list:
                self.active_towers_list.remove(sim)

        else:  # status == "DISABLED"
            color = self.COLOR_DISABLED
            sim.operational = False

            # Remove from tower lists so UEs don't consider this tower
            if sim in GLOBAL_TOWERS:
                GLOBAL_TOWERS.remove(sim)
            if sim in self.active_towers_list:
                self.active_towers_list.remove(sim)

            # Disconnect this tower from all connected towers
            for other in list(sim.connected_towers):
                if sim in other.connected_towers:
                    other.connected_towers.remove(sim)
                sim.connected_towers.remove(other)

            # Detach any UEs currently attached to this tower and clear their UE→tower line
            for ue_data in self.user_equipment:
                ue_sim = ue_data["sim_object"]
                if getattr(ue_sim, "current_tower", None) is sim:
                    ue_sim.current_tower = None
                    if ue_data.get("conn_line_id"):
                        self.canvas.delete(ue_data["conn_line_id"])
                        ue_data["conn_line_id"] = None


        self.canvas.itemconfig(hex_id, fill=color)
        data["status"] = status

        # UEs must recompute their internal attachment when tower set changes
        current_tower_list = list(GLOBAL_TOWERS)

        for ue_data in self.user_equipment:
            ue = ue_data["sim_object"]

            try:
                ue.update_towers(current_tower_list)
            except Exception as e:
                print("UE update_towers error in set_tower_status:", e)

            # If UE is no longer attached, remove green line
            if ue.current_tower is None and ue_data.get("conn_line_id"):
                self.canvas.delete(ue_data["conn_line_id"])
                ue_data["conn_line_id"] = None


        # -----------------------------
        # REDRAW LINKS (BLUE + GREEN)
        # -----------------------------
        self.draw_tower_links()
        self.refresh_all_connection_lines()

        self.status_var.set(f"{data['id']} (IP {data['ip_addr']}) → {status}")


        self.status_var.set(f"{data['id']} (IP {data['ip_addr']}) → {status}")

    # ----------------------------------------------------------------------
    # Tower UI Panel (Connect / Disconnect / Rates)
    # ----------------------------------------------------------------------
    def on_tower_click(self, hex_id):
        data = self.towers.get(hex_id)
        if not data:
            return

        tower_sim = data["sim_object"]

        top = tk.Toplevel(self.root)
        top.title(f"Control: {data['id']}")
        top.geometry("360x560")       # Taller window
        top.configure(bg=self.UI_COLOR)

        # --------------------
        # TOWER BASIC INFO
        # --------------------
        tk.Label(
            top, text=f"Tower IP: {int_to_ip(data['ip_addr'])}",
            font=("Arial", 12, "bold"), bg=self.UI_COLOR
        ).pack(pady=10)

        tk.Label(
            top, text=f"Grid: R{data['row']} → C{data['col']}", bg=self.UI_COLOR
        ).pack()

        tk.Label(
            top, text=f"Canvas: X={int(data['x'])}, Y={int(data['y'])}", bg=self.UI_COLOR
        ).pack()

        # --------------------
        # LIVE RATE LABELS
        # --------------------
        cur_label = tk.Label(top, text="Current Data Rate: 0.000 Mbps", bg=self.UI_COLOR)
        cur_label.pack()
        max_label = tk.Label(
            top,
            text=f"Max Data Rate: {(tower_sim.max_data_rate * 1e-6):.3f} Mbps",
            bg=self.UI_COLOR
        )
        max_label.pack()

        def update_live_rate():
            if not top.winfo_exists():
                return
            cur_mbps = getattr(tower_sim, "gui_last_n_tx_bytes", 0) * 8e-6
            cur_label.config(text=f"Current Data Rate: {cur_mbps:.3f} Mbps")
            top.after(300, update_live_rate)

        update_live_rate()

        # --------------------
        # STATUS CONTROLS
        # --------------------
        tk.Label(
            top, text="Tower Status:", font=("Arial", 10), bg=self.UI_COLOR
        ).pack(pady=6)

        status_lbl = tk.Label(
            top,
            text=f"Current Status: {data['status']}",
            font=("Arial", 10, "italic"),
            fg="#3498db",
            bg=self.UI_COLOR,
        )
        status_lbl.pack()

        def upd(s):
            self.set_tower_status(hex_id, s)
            status_lbl.config(text=f"Current Status: {s}")

        btnframe = tk.Frame(top, bg=self.UI_COLOR)
        btnframe.pack(fill=tk.X, padx=20, pady=4)

        tk.Button(
            btnframe, text="ACTIVE", bg=self.COLOR_ACTIVE, fg="white",
            command=lambda: upd("ACTIVE"), relief=tk.FLAT
        ).pack(fill=tk.X, pady=2)

        tk.Button(
            btnframe, text="OUTAGE", bg=self.COLOR_OUTAGE, fg="white",
            command=lambda: upd("OUTAGE"), relief=tk.FLAT
        ).pack(fill=tk.X, pady=2)

        tk.Button(
            btnframe, text="DISABLE", bg=self.COLOR_DISABLED, fg="black",
            command=lambda: upd("DISABLED"), relief=tk.FLAT
        ).pack(fill=tk.X, pady=2)

        # --------------------------
        # CONNECT TO ANOTHER TOWER
        # --------------------------
        tk.Label(
            top,
            text="Connect This Tower To:",
            font=("Arial", 10),
            bg=self.UI_COLOR
        ).pack(pady=(10, 2))

        connected = set(getattr(tower_sim, "connected_towers", []))
        candidates = []

        for h2, d2 in self.towers.items():
            if h2 == hex_id:
                continue
            other = d2["sim_object"]

            # ONLY ACTIVE towers and not already connected
            if d2["status"] == "ACTIVE" and other not in connected:
                candidates.append((int_to_ip(d2["ip_addr"]), other))

        candidates.sort(key=lambda x: x[0])
        connect_vals = [str(ip) for ip, _ in candidates] or ["(none available)"]

        conn_var = tk.StringVar(value=connect_vals[0])
        conn_box = ttk.Combobox(
            top, textvariable=conn_var, values=connect_vals, state="readonly"
        )
        conn_box.pack(fill=tk.X, padx=20, pady=4)

        def do_connect():
            ip_str = conn_var.get()
            if ip_str == "(none available)":
                return
            for ip, other in candidates:
                if str(ip) == ip_str:
                    tower_sim.connect_tower(other)
                    self.status_var.set(
                        f"Tower {int_to_ip(data['ip_addr'])} connected to Tower {ip_str}."
                    )

        tk.Button(
            top,
            text="Connect",
            bg="#2980b9",
            fg="white",
            relief=tk.FLAT,
            command=do_connect,
        ).pack(fill=tk.X, padx=20, pady=(2, 10))

        # --------------------------
        # DISCONNECT FROM CONNECTED TOWERS
        # --------------------------
        tk.Label(
            top,
            text="Disconnect This Tower From:",
            font=("Arial", 10),
            bg=self.UI_COLOR
        ).pack(pady=(10, 2))

        # Build list of connected towers
        disc_list = []
        for other in getattr(tower_sim, "connected_towers", []):
            for h2, d2 in self.towers.items():
                if d2["sim_object"] is other:
                    disc_list.append((d2["ip_addr"], other))
                    break

        disc_list.sort(key=lambda x: x[0])
        disc_vals = [str(ip) for ip, _ in disc_list] or ["(no connected towers)"]

        disc_var = tk.StringVar(value=disc_vals[0])
        disc_box = ttk.Combobox(
            top, textvariable=disc_var, values=disc_vals, state="readonly"
        )
        disc_box.pack(fill=tk.X, padx=20, pady=4)

        def do_disconnect():
            ip_str = disc_var.get()
            if ip_str == "(no connected towers)":
                return
            for ip, other in disc_list:
                if str(ip) == ip_str:
                    # Bi-directional removal
                    if other in tower_sim.connected_towers:
                        tower_sim.connected_towers.remove(other)
                    if tower_sim in getattr(other, "connected_towers", []):
                        other.connected_towers.remove(tower_sim)
                    self.status_var.set(
                        f"Disconnected Tower {data['ip_addr']} from Tower {ip_str}."
                    )


        tk.Button(
            top,
            text="Disconnect",
            bg="#7f8c8d",
            fg="white",
            relief=tk.FLAT,
            command=do_disconnect,
        ).pack(fill=tk.X, padx=20, pady=(2, 10))

        # --------------------------
        # DELETE TOWER
        # --------------------------
        tk.Button(
            top,
            text="DELETE TOWER",
            bg="#c0392b",
            fg="white",
            relief=tk.FLAT,
            command=lambda: self.delete_tower(hex_id, top),
        ).pack(fill=tk.X, padx=20, pady=(12, 5))

        # CLOSE
        tk.Button(
            top,
            text="Close",
            bg="#bdc3c7",
            relief=tk.FLAT,
            command=top.destroy,
        ).pack(pady=5)

    # ----------------------------------------------------------------------
    # OUTAGE + RESET UEs
    # ----------------------------------------------------------------------
    def trigger_total_outage(self):
        """
        Begin a total outage lasting N timesteps.
        ONLY ACTIVE towers should enter OUTAGE mode.
        """
        if not self.sim_running:
            messagebox.showinfo(
                "Simulation not running",
                "Start the simulation before triggering an outage."
            )
            return

        try:
            steps = int(self.outage_steps_var.get())
            if steps <= 0:
                raise ValueError
        except ValueError:
            messagebox.showwarning("Invalid Steps", "Please enter a positive integer.")
            return

        # Save previous states only for ACTIVE towers
        self._outage_prev_status = {}
        for hex_id, data in self.towers.items():
            if data["status"] == "ACTIVE":
                self._outage_prev_status[hex_id] = "ACTIVE"
                self.set_tower_status(hex_id, "OUTAGE")

        self._outage_remaining = steps
        self.status_var.set(f"Simulating total outage for {steps} timesteps.")

        self.draw_tower_links()

    def disable_all_towers(self):
        for hex_id in self.towers:
            self.set_tower_status(hex_id, "DISABLED")

        # Force GUI cleanup
        for ue_data in self.user_equipment:
            ue = ue_data["sim_object"]
            ue.update_towers([])
            if ue_data.get("conn_line_id"):
                self.canvas.delete(ue_data["conn_line_id"])
                ue_data["conn_line_id"] = None

        self.draw_tower_links()
        self.refresh_all_connection_lines()

        self.status_var.set("All towers disabled.")


    def reset_all_ues_tx(self):
        """Reset all UE transmit settings to NONE."""
        for ue in GLOBAL_UES:
            ue.tx_target_ip = None
            ue.tx_mode = "fixed"
            ue.tx_n_bytes = 0
            ue.gui_last_n_tx_bytes = 0
            ue.clear_buffer()
        self.status_var.set("All UE TX settings reset.")

    # ----------------------------------------------------------------------
    # SIMULATION LOOP CONTROL
    # ----------------------------------------------------------------------
    def start_simulation(self):
        if self.sim_running:
            return

        if not self.active_towers_list:
            messagebox.showwarning(
                "No active towers",
                "Enable at least one tower before starting the simulation."
            )
            return

        GLOBAL_TOWERS.clear()
        GLOBAL_TOWERS.extend(self.active_towers_list)

        GLOBAL_UES.clear()
        GLOBAL_UES.extend(self.active_ues_list)

        for t in GLOBAL_TOWERS:
            t.tx_attempts = len(GLOBAL_TOWERS)+1

        self.sim_running = True
        self.sim_thread = threading.Thread(
            target=self.simulation_loop, daemon=True
        )
        self.sim_thread.start()
        self.status_var.set("Simulation running...")

        for ue in GLOBAL_UES:
            ue.update_towers(list(GLOBAL_TOWERS))
            ue.t_step = 0


    def stop_simulation(self):
        self.sim_running = False
        self.status_var.set("Simulation stopping...")

    # ----------------------------------------------------------------------
    # DYNAMIC UE LABEL UPDATE (IP, Max, CR, Actual)
    # ----------------------------------------------------------------------
    def _update_ue_labels(self):
        for ue_data in self.user_equipment:
            ue = ue_data["sim_object"]
            lbl = ue_data["label_id"]

            cr = getattr(ue, "code_rate", 1.0)
            ber = getattr(ue, "ber", 0)
            max_effective = ue.max_data_rate * cr
            max_mbps = max_effective * 1e-6

            actual_mbps = getattr(ue, "gui_last_n_tx_bytes", 0) * 8e-6

            cr_str = f"{cr:.3f}" if isinstance(cr, (int, float)) else "N/A"

            self.canvas.itemconfig(
                lbl,
                text=(
                    f"IP: {int_to_ip(ue.ip_addr)} | Max: {max_mbps:.1f} Mbps | CR: {cr_str}\n"
                    f"Actual: {actual_mbps:.3f} Mbps | BER: {ber*1e5:.3f}E-5"
                )
            )

    # ----------------------------------------------------------------------
    # MAIN SIMULATION LOOP
    # ----------------------------------------------------------------------
    def simulation_loop(self):
        timestep = 0
        last_print_time = time.time()

        while self.sim_running:

            # ------------------------------------
            # READ steps per second from the GUI
            # ------------------------------------
            try:
                n_steps = int(self.steps_per_sec_var.get())
                if n_steps < 1:
                    n_steps = 1
            except:
                n_steps = 1

            # ------------------------------------
            # UPDATE t_delta for UE and Tower
            # ------------------------------------
            new_t_delta = 1.0 / n_steps
            for ue in GLOBAL_UES:
                ue.t_delta = new_t_delta
            for tower in GLOBAL_TOWERS:
                tower.t_delta = new_t_delta

            # ------------------------------------
            # RUN n_steps simulation steps per second
            # ------------------------------------
            for _ in range(n_steps):

                towers = list(GLOBAL_TOWERS)
                ues = list(GLOBAL_UES)

                # UPDATE ALL UE TIMESTEPS (CRITICAL FOR ARQ)
                for ue in ues:
                    ue.t_step = timestep

                # -----------------------------------------------------------
                # TOTAL OUTAGE HANDLER
                # -----------------------------------------------------------
                if self._outage_remaining > 0:
                    for ue in ues:
                        try:
                            ue.update_towers([])
                        except:
                            pass

                    self._outage_remaining -= 1

                    # Outage finished
                    if self._outage_remaining == 0:
                        for hex_id, prev_status in self._outage_prev_status.items():
                            self.set_tower_status(hex_id, prev_status)

                        for ue in ues:
                            try:
                                ue.update_towers(GLOBAL_TOWERS)
                            except:
                                pass

                        self.status_var.set("Outage simulation complete. Towers restored.")

                # -----------------------------------------------------------
                # APPLY UE TRANSMIT MODES
                # -----------------------------------------------------------
                for ue in ues:
                    try:
                        dest_ip = ue.tx_target_ip
                        if dest_ip is None:
                            continue

                        mode = ue.tx_mode
                        if mode == "fixed":
                            if ue.tx_n_bytes > 0:
                                ue.set_tx_bytes(ue.tx_n_bytes, dest_ip)

                        elif mode == "random":
                            ue.set_tx_bytes(random.randint(1, 65535), dest_ip)

                        elif mode == "max":
                            cr = getattr(ue, "code_rate", 1.0)
                            max_bps = ue.max_data_rate * cr
                            # nbytes = int(max_bps * ue.t_delta / 8.0 * 0.95)
                            nbytes = int(max_bps * ue.t_delta / 8.0)
                            print(f"UE {int_to_ip(ue.ip_addr)}: Transmitting {nbytes} bytes")
                            if nbytes > 0:
                                ue.set_tx_bytes(nbytes, dest_ip)

                    except Exception as e:
                        print("UE TX ERR:", e)

                # -----------------------------------------------------------
                # UE STEP
                # -----------------------------------------------------------
                simulate_noise = (self.simulate_noise_var.get() == "True")

                for ue in ues:
                    try:
                        ue.step(simulate_noise)
                    except Exception as e:
                        print("UE STEP ERR:", e)

                # -----------------------------------------------------------
                # TOWER TX LOOP
                # -----------------------------------------------------------
                transmitting = True
                while transmitting:
                    transmitting = False
                    for tower in towers:
                        try:
                            if tower.can_transmit():
                                tower.step(simulate_noise)
                                transmitting = True
                        except Exception as e:
                            print("TOWER STEP ERR:", e)

                # -----------------------------------------------------------
                # RECORD tx_bytes BEFORE CLEARING
                # -----------------------------------------------------------
                for tower in towers:
                    tower.gui_last_n_tx_bytes = getattr(tower, "n_tx_bytes", 0) * float(self.steps_per_sec_var.get())

                for ue in ues:
                    ue.gui_last_n_tx_bytes = getattr(ue, "tx_bytes_step", 0) * float(self.steps_per_sec_var.get())

                # -----------------------------------------------------------
                # AUTO WARNING COLOR (>= 50% LOAD) - VISUAL ONLY
                # -----------------------------------------------------------
                for hex_id, data in self.towers.items():
                    sim = data["sim_object"]

                    if data["status"] not in ("ACTIVE", "WARNING"):
                        continue

                    try:
                        used_bps = sim.gui_last_n_tx_bytes
                        max_bps = sim.max_data_rate
                        utilization = used_bps / max_bps if max_bps > 0 else 0.0

                        # Only adjust COLOR, DO NOT change status or GLOBAL_TOWERS
                        if utilization >= 0.5:
                            self.canvas.itemconfig(hex_id, fill=self.COLOR_WARNING)
                        else:
                            self.canvas.itemconfig(hex_id, fill=self.COLOR_ACTIVE)

                    except:
                        pass

                # -----------------------------------------------------------
                # ADVANCE SIM TIME
                # -----------------------------------------------------------
                self.env.now += new_t_delta
                timestep += 1

                # -----------------------------------------------------------
                # GUI REFRESH
                # -----------------------------------------------------------
                self.root.after(0, self.refresh_all_connection_lines)
                self.root.after(0, self.draw_tower_links)
                self.root.after(0, self._update_ue_labels)

                # -----------------------------------------------------------
                # PRINT ONCE PER REAL SECOND
                # -----------------------------------------------------------
                current_time = time.time()
                if current_time - last_print_time >= 1.0:

                    for tower in towers:
                        dr = tower.gui_last_n_tx_bytes * 8e-6
                        mx = tower.max_data_rate * 1e-6
                        print(
                            f"{self.env.now:.2f}s: Tower IP_ADDR {int_to_ip(tower.ip_addr)}: "
                            f"Data rate = {dr:.3f} Mbps, Max = {mx:.3f} Mbps, "
                            f"Bit-error rate = {tower.ber*1e5:.3f}E-5"
                        )

                    for ue in ues:
                        if ue.current_tower:
                            dr = ue.gui_last_n_tx_bytes * 8e-6
                            mx = ue.max_data_rate * (ue.code_rate or 1) * 1e-6
                            print(
                                f"{self.env.now:.2f}s: UE IP_ADDR {int_to_ip(ue.ip_addr)}: "
                                f"Tower IP_ADDR = {int_to_ip(ue.current_tower.ip_addr)} "
                                f"Band = {ue.freq_band}, Code rate = {ue.code_rate}, "
                                f"Data rate = {dr:.3f} Mbps, Max = {mx:.3f} Mbps, "
                                f"Bit-error rate = {ue.ber*1e5:.5f}E-5"
                            )

                    print(f"Timestep {timestep} Completed.")
                    print("----------------------------------------------------------------------------------------")
                    last_print_time = current_time

                # -----------------------------------------------------------
                # CLEAR COUNTS
                # -----------------------------------------------------------
                for tower in towers:
                    tower.clear_tx_count()
                for ue in ues:
                    ue.clear_tx_count()

                # RUN NEXT STEP WITHOUT PAUSING A WHOLE SECOND
                time.sleep(1.0 / n_steps)

            # End of while → clean shutdown
            self.root.after(0, lambda: self.status_var.set("Simulation stopped."))


# ----------------------------------------------------------------------
# MAIN ENTRY POINT
# ----------------------------------------------------------------------
if __name__ == "__main__":
    root = tk.Tk()
    app = NetworkSimulationApp(root)
    root.mainloop()
