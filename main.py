import time
import threading
import ctypes
from ctypes import POINTER, cast
import comtypes
from comtypes import CLSCTX_ALL
from pycaw.pycaw import AudioUtilities, IMMDeviceEnumerator
from pycaw.constants import CLSID_MMDeviceEnumerator
from pycaw.pycaw import IAudioMeterInformation as PycawIAudioMeterInformation
import pystray
from PIL import Image, ImageDraw
import argparse
import sys
import json
import os
import tkinter as tk
from tkinter import ttk, messagebox, colorchooser

# Use IAudioMeterInformation from pycaw
IAudioMeterInformation = PycawIAudioMeterInformation

# Config paths
CONFIG_DIR = os.path.join(os.getenv('APPDATA') or os.path.expanduser('~'), 'VU_Meter')
CONFIG_PATH = os.path.join(CONFIG_DIR, 'config.json')

def load_config():
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def save_config(devices_ordered):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        # Only persist devices with their embedded settings (gain, curve, width, colors)
        payload = {
            'devices': devices_ordered
        }
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(payload, f, indent=2)
        return True
    except Exception:
        return False


def list_all_devices():
    # Ensure COM is initialized for the calling thread (safe to call multiple times)
    coinit = False
    try:
        try:
            comtypes.CoInitialize()
            coinit = True
        except Exception:
            pass
        try:
            devs = AudioUtilities.GetAllDevices()
        except Exception:
            devs = []
        devices = []
        for d in devs:
            name = getattr(d, 'FriendlyName', None) or getattr(d, 'friendly_name', None) or str(d)
            did = getattr(d, 'id', None) or getattr(d, 'Id', None)
            if not did and hasattr(d, 'GetId'):
                try:
                    did = d.GetId()
                except Exception:
                    did = None
            if did:
                devices.append({'id': did, 'name': name})
        return devices
    finally:
        if coinit:
            try:
                comtypes.CoUninitialize()
            except Exception:
                pass

# --- Get default render device ---

def get_default_render_device_id():
    coinit = False
    try:
        try:
            comtypes.CoInitialize()
            coinit = True
        except Exception:
            pass
        try:
            enumerator = comtypes.CoCreateInstance(
                CLSID_MMDeviceEnumerator,
                IMMDeviceEnumerator,
                CLSCTX_ALL
            )
            dev = enumerator.GetDefaultAudioEndpoint(0, 1)
            did = dev.GetId()
            try:
                enumerator.Release()
            except Exception:
                pass
            return did
        except Exception:
            return None
    finally:
        if coinit:
            try:
                comtypes.CoUninitialize()
            except Exception:
                pass

# Argument parsing for device selection/listing
parser = argparse.ArgumentParser(description="System tray VU meter using pycaw")
parser.add_argument("--list-devices", action="store_true", help="List available audio endpoint devices and exit")
parser.add_argument("--devices", nargs="+", help="One or more device indices or name substrings. Omit to use default render device")
parser.add_argument("--gains", nargs="+", type=float, help="Per-device gains (one per device). If fewer than devices, remaining default to 1.0")
args = parser.parse_args()



# Handle device listing
if args.list_devices:
    # Prefer our helper that resolves stable endpoint IDs
    devices_simple = list_all_devices()
    # Try also to get states via pycaw objects, aligned by index
    try:
        pycaw_devs = AudioUtilities.GetAllDevices()
    except Exception:
        pycaw_devs = []
    if not devices_simple:
        print("No devices found.")
    else:
        for idx, d in enumerate(devices_simple):
            name = d.get('name')
            did = d.get('id')
            state = None
            if idx < len(pycaw_devs):
                try:
                    state = getattr(pycaw_devs[idx], 'State', None)
                except Exception:
                    state = None
            state_part = f" (state={state})" if state is not None else ""
            print(f"[{idx}] {name} | id={did}{state_part}")
    sys.exit(0)

selected_imm_device = None


# Resolve list of selected device endpoint IDs (strings). If none specified, use default endpoint only.
selected_ids = []

if args.devices:
    try:
        all_devices = AudioUtilities.GetAllDevices()
    except Exception:
        all_devices = []
    for token in args.devices:
        token = token.strip()
        chosen = None
        if token.isdigit():
            i = int(token)
            if 0 <= i < len(all_devices):
                chosen = all_devices[i]
        else:
            token_l = token.lower()
            matches = [d for d in all_devices if token_l in ((getattr(d, "FriendlyName", None) or getattr(d, "friendly_name", None) or str(d)).lower())]
            if len(matches) == 1:
                chosen = matches[0]
            elif len(matches) > 1:
                # If multiple matches, pick the first for now
                chosen = matches[0]
        if chosen is not None:
            eid = getattr(chosen, "id", None) or getattr(chosen, "Id", None)
            if not eid and hasattr(chosen, "GetId"):
                try:
                    eid = chosen.GetId()
                except Exception:
                    eid = None
            if eid:
                selected_ids.append(eid)

# If no CLI devices provided, try to load configuration
if (not args.devices):
    cfg = load_config()
    if cfg:
        try:
            devices_cfg = cfg.get('devices') or []
            ids_cfg = [d.get('id') for d in devices_cfg if isinstance(d, dict) and d.get('id')]
            if ids_cfg:
                selected_ids = ids_cfg
        except Exception:
            pass

# Fallback to default device if still none selected
if not selected_ids:
    did = get_default_render_device_id()
    if did:
        selected_ids.append(did)

# --- Tray Icon handling ---

def _parse_color(c, default):
    try:
        if isinstance(c, (tuple, list)) and len(c) >= 3:
            return (int(c[0]) & 255, int(c[1]) & 255, int(c[2]) & 255)
        if isinstance(c, str):
            s = c.strip()
            if s.startswith('#'):
                s = s[1:]
            if len(s) == 6:
                return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except Exception:
        pass
    return default


def create_multi_icon(levels, settings=None):
    size = 32
    width = size
    height = size
    n = max(1, len(levels))
    # Determine per-bar widths. If settings specify positive widths, use them; else equal split with remainder to first.
    specified = []
    remaining = width
    if settings and len(settings) >= n:
        for i in range(n):
            w = max(0, int(settings[i].get('width', 0) or 0))
            specified.append(w)
            remaining -= w
    if not settings or len(settings) < n or remaining < 0 or all(w <= 0 for w in specified):
        # fallback equal split
        base = width // n
        rem = width - base * n
        first = base + rem
        bar_widths = [first] + [base] * (n - 1)
    else:
        # distribute remaining equally among bars with zero/unspecified width; first gets remainder
        zeros = [i for i, w in enumerate(specified) if w == 0]
        if zeros:
            base = remaining // len(zeros)
            rem = remaining - base * len(zeros)
            for k, i in enumerate(zeros):
                add = base + (rem if k == 0 else 0)
                specified[i] = add
        bar_widths = specified[:n]
    img = Image.new('RGB', (width, height), (0, 0, 0))
    draw = ImageDraw.Draw(img)
    x = 0
    for i, lvl in enumerate(levels):
        w = bar_widths[i]
        if w <= 0:
            continue
        lvl_clamped = max(0.0, min(1.0, float(lvl)))
        # Nonlinear display curve: x^(1/f)
        f = 1.0
        if settings and i < len(settings):
            try:
                f = float(settings[i].get('curve', 1.0))
            except Exception:
                f = 1.0
            if f <= 0:
                f = 1.0
        disp = pow(lvl_clamped, 1.0 / f)
        h = int(round(disp * height))
        if h <= 0:
            x += w
            continue
        if h > height:
            h = height
        y0 = height - h
        y1 = height - 1
        # Colors per device
        if settings and i < len(settings):
            cols = settings[i].get('colors') or {}
            low = _parse_color(cols.get('low'), (0, 255, 0))
            mid = _parse_color(cols.get('mid'), (255, 255, 0))
            high = _parse_color(cols.get('high'), (255, 0, 0))
        else:
            low, mid, high = (0, 255, 0), (255, 255, 0), (255, 0, 0)
        color = low if disp < 0.8 else (mid if disp < 0.9 else high)
        draw.rectangle([x, y0, x + w - 1, y1], fill=color)
        x += w
    return img


def update(icon, endpoint_ids, settings, stop_event):
    # Initialize COM and activate meters for each endpoint in this thread
    comtypes.CoInitialize()
    enumerator = None
    meters = []
    try:
        enumerator = comtypes.CoCreateInstance(
            CLSID_MMDeviceEnumerator,
            IMMDeviceEnumerator,
            CLSCTX_ALL
        )
        for eid in endpoint_ids:
            try:
                dev = enumerator.GetDevice(eid)
            except Exception:
                dev = enumerator.GetDefaultAudioEndpoint(0, 1)
            m = dev.Activate(IAudioMeterInformation._iid_, CLSCTX_ALL, None)
            m = cast(m, POINTER(IAudioMeterInformation))
            meters.append(m)

        while not stop_event.is_set():
            levels = []
            for i, m in enumerate(meters):
                try:
                    lvl = m.GetPeakValue()
                except Exception:
                    lvl = 0.0
                # Apply per-device gain then clamp
                gain = 1.0
                if settings and i < len(settings):
                    try:
                        gain = float(settings[i].get('gain', gain))
                    except Exception:
                        gain = 1.0
                lvl_scaled = max(0.0, min(1.0, lvl * gain))
                levels.append(lvl_scaled)
            icon.icon = create_multi_icon(levels, settings)
            try:
                icon.update_icon()
            except Exception:
                pass
            time.sleep(0.05)
    finally:
        # Release COM interfaces while the apartment is still initialized
        for m in meters:
            try:
                m.Release()
            except Exception:
                pass
        if enumerator is not None:
            try:
                enumerator.Release()
            except Exception:
                pass
        try:
            comtypes.CoUninitialize()
        except Exception:
            pass

# Event to coordinate shutdown between tray and worker thread
stop_event = threading.Event()

def on_exit(icon, item):
    # Signal worker thread to stop, then stop tray loop
    try:
        stop_event.set()
    except Exception:
        pass
    icon.stop()

# Build per-device settings aligned with selected_ids
settings_from_cfg = None
if (not args.devices):
    cfg = load_config()
    if cfg:
        try:
            devices_cfg = cfg.get('devices') or []
            # normalize
            norm = []
            for d in devices_cfg:
                if not isinstance(d, dict):
                    continue
                norm.append({
                    'id': d.get('id'),
                    'name': d.get('name', ''),
                    'gain': float(d.get('gain', 1.0)) if str(d.get('gain', '')).strip() != '' else 1.0,
                    'curve': float(d.get('curve', 1.0)) if str(d.get('curve', '')).strip() != '' else 1.0,
                    'width': int(d.get('width', 0) or 0),
                    'colors': d.get('colors') or {}
                })
            settings_from_cfg = norm
        except Exception:
            settings_from_cfg = None

# Start with default settings
_device_settings = []
for i, eid in enumerate(selected_ids):
    entry = {
        'id': eid,
        'name': '',
        'gain': 1.0,
        'curve': 1.0,
        'width': 0,
        'colors': {}
    }
    if settings_from_cfg and i < len(settings_from_cfg):
        sc = settings_from_cfg[i]
        if sc.get('id') == eid:
            entry.update({k: sc.get(k, entry[k]) for k in ('name','gain','curve','width','colors')})
    _device_settings.append(entry)

# If CLI gains are provided, override gains of first N devices
if args.gains:
    for i, g in enumerate(args.gains):
        if i < len(_device_settings):
            try:
                _device_settings[i]['gain'] = float(g)
            except Exception:
                pass

# Globals for restart capability
_selected_ids = selected_ids
_worker = None

def start_worker():
    global _worker
    # ensure previous worker stopped
    if _worker and _worker.is_alive():
        try:
            stop_event.set()
        except Exception:
            pass
        time.sleep(0.1)
    try:
        stop_event.clear()
    except Exception:
        pass
    _worker = threading.Thread(target=update, args=(icon, _selected_ids, [
        {'gain': d.get('gain', 1.0), 'curve': d.get('curve', 1.0), 'width': d.get('width', 0), 'colors': d.get('colors', {})}
        for d in _device_settings
    ], stop_event), daemon=True)
    _worker.start()


def restart_worker(new_ids, new_settings):
    global _selected_ids, _device_settings
    _selected_ids = list(new_ids)
    _device_settings = list(new_settings)
    try:
        stop_event.set()
    except Exception:
        pass
    # give the thread a moment to exit its loop
    time.sleep(0.2)
    start_worker()

# Settings window

def open_settings_window():
    # Initialize COM in this UI thread for device enumeration and any COM calls
    coinit = False
    try:
        try:
            comtypes.CoInitialize()
            coinit = True
        except Exception:
            pass
        devices = list_all_devices()
        # Map id->name for quick lookup
        id_to_name = {d['id']: d['name'] for d in devices}
    finally:
        # Do not uninitialize here yet because Tk window may interact further; we'll uninit on close
        pass

    # Build initial selected list with names
    initial_selected = []
    for eid in _selected_ids:
        initial_selected.append({'id': eid, 'name': id_to_name.get(eid, eid)})

    gains_map = {d['id']: d.get('gain', 1.0) for d in _device_settings}
    curve_map = {d['id']: d.get('curve', 1.0) for d in _device_settings}
    width_map = {d['id']: d.get('width', 0) for d in _device_settings}
    colors_map = {d['id']: (d.get('colors') or {}) for d in _device_settings}

    root = tk.Tk()
    root.title('VU Meter Settings')
    root.geometry('720x460')

    # Frames
    left = ttk.Frame(root)
    mid = ttk.Frame(root)
    right = ttk.Frame(root)
    bottom = ttk.Frame(root)
    left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=8, pady=8)
    mid.pack(side=tk.LEFT, fill=tk.Y, padx=4)
    right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=8, pady=8)
    bottom.pack(side=tk.BOTTOM, fill=tk.X, padx=8, pady=8)

    ttk.Label(left, text='Available devices').pack(anchor='w')
    avail = tk.Listbox(left, selectmode=tk.EXTENDED, exportselection=False)
    avail.pack(fill=tk.BOTH, expand=True)
    for d in devices:
        avail.insert(tk.END, d['name'])

    ttk.Label(right, text='Selected devices (order = bar order)').pack(anchor='w')
    sel = tk.Listbox(right, selectmode=tk.SINGLE, exportselection=False)
    sel.pack(fill=tk.BOTH, expand=True)
    for d in initial_selected:
        sel.insert(tk.END, d['name'])

    # Buttons between lists
    def add_selected():
        selected_idxs = avail.curselection()
        names = [avail.get(i) for i in selected_idxs]
        # map names to ids (first match)
        name_to_id = {d['name']: d['id'] for d in devices}
        for nm in names:
            did = name_to_id.get(nm)
            if did is None:
                continue
            sel.insert(tk.END, nm)
            if did not in gains_map:
                gains_map[did] = 1.0
            initial_selected.append({'id': did, 'name': nm})

    def remove_selected():
        i = sel.curselection()
        if not i:
            return
        idx = i[0]
        removed = initial_selected.pop(idx)
        sel.delete(idx)
        # keep gain map; it will be filtered on save
        if idx > 0:
            sel.selection_set(idx - 1)

    def move_up():
        i = sel.curselection()
        if not i or i[0] == 0:
            return
        idx = i[0]
        item = initial_selected.pop(idx)
        initial_selected.insert(idx - 1, item)
        nm = sel.get(idx)
        sel.delete(idx)
        sel.insert(idx - 1, nm)
        sel.selection_set(idx - 1)

    def move_down():
        i = sel.curselection()
        if not i or i[0] == sel.size() - 1:
            return
        idx = i[0]
        item = initial_selected.pop(idx)
        initial_selected.insert(idx + 1, item)
        nm = sel.get(idx)
        sel.delete(idx)
        sel.insert(idx + 1, nm)
        sel.selection_set(idx + 1)

    btn_add = ttk.Button(mid, text='>>', command=add_selected)
    btn_remove = ttk.Button(mid, text='<<', command=remove_selected)
    btn_up = ttk.Button(mid, text='Up', command=move_up)
    btn_down = ttk.Button(mid, text='Down', command=move_down)
    for b in (btn_add, btn_remove, btn_up, btn_down):
        b.pack(pady=6)

    # Per-device editors
    gain_var = tk.StringVar(value='1.0')
    curve_var = tk.StringVar(value='1.0')
    width_var = tk.StringVar(value='0')
    color_low_var = tk.StringVar(value='#00FF00')
    color_mid_var = tk.StringVar(value='#FFFF00')
    color_high_var = tk.StringVar(value='#FF0000')

    last_selected_idx = {'idx': -1}
    def on_sel_change(event=None):
        i = sel.curselection()
        if not i:
            return
        idx = i[0]
        last_selected_idx['idx'] = idx
        if idx < len(initial_selected):
            did = initial_selected[idx]['id']
            gain_var.set(str(gains_map.get(did, 1.0)))
            curve_var.set(str(curve_map.get(did, 1.0)))
            width_var.set(str(width_map.get(did, 0)))
            cols = colors_map.get(did, {})
            color_low_var.set(str(cols.get('low', '#00FF00')))
            color_mid_var.set(str(cols.get('mid', '#FFFF00')))
            color_high_var.set(str(cols.get('high', '#FF0000')))
    sel.bind('<<ListboxSelect>>', on_sel_change)

    # Build right-side editor grid
    edit = ttk.Frame(right)
    edit.pack(fill=tk.X, pady=6)

    def picker(var):
        try:
            rgb, hx = colorchooser.askcolor(color=var.get() or '#FFFFFF', title='Pick color')
            if hx:
                var.set(hx)
        except Exception:
            pass

    # Gain
    row1 = ttk.Frame(edit); row1.pack(fill=tk.X, pady=2)
    ttk.Label(row1, text='Gain').pack(side=tk.LEFT, padx=(0,6))
    ttk.Entry(row1, textvariable=gain_var, width=8).pack(side=tk.LEFT)
    ttk.Button(row1, text='Set', command=lambda: _apply_for_selected(sel, initial_selected, gains_map, gain_var, float, 'Gain')).pack(side=tk.LEFT, padx=6)

    # Curve
    row2 = ttk.Frame(edit); row2.pack(fill=tk.X, pady=2)
    ttk.Label(row2, text='Curve f').pack(side=tk.LEFT, padx=(0,6))
    ttk.Entry(row2, textvariable=curve_var, width=8).pack(side=tk.LEFT)
    ttk.Button(row2, text='Set', command=lambda: _apply_for_selected(sel, initial_selected, curve_map, curve_var, float, 'Curve')).pack(side=tk.LEFT, padx=6)

    # Width
    row3 = ttk.Frame(edit); row3.pack(fill=tk.X, pady=2)
    ttk.Label(row3, text='Width px (0=auto)').pack(side=tk.LEFT, padx=(0,6))
    ttk.Entry(row3, textvariable=width_var, width=8).pack(side=tk.LEFT)
    ttk.Button(row3, text='Set', command=lambda: _apply_for_selected(sel, initial_selected, width_map, width_var, int, 'Width')).pack(side=tk.LEFT, padx=6)

    # Colors
    row4 = ttk.Frame(edit); row4.pack(fill=tk.X, pady=2)
    ttk.Label(row4, text='Colors:').pack(side=tk.LEFT)
    ttk.Entry(row4, textvariable=color_low_var, width=9).pack(side=tk.LEFT, padx=2)
    ttk.Button(row4, text='…', width=3, command=lambda: picker(color_low_var)).pack(side=tk.LEFT)
    ttk.Entry(row4, textvariable=color_mid_var, width=9).pack(side=tk.LEFT, padx=2)
    ttk.Button(row4, text='…', width=3, command=lambda: picker(color_mid_var)).pack(side=tk.LEFT)
    ttk.Entry(row4, textvariable=color_high_var, width=9).pack(side=tk.LEFT, padx=2)
    ttk.Button(row4, text='…', width=3, command=lambda: picker(color_high_var)).pack(side=tk.LEFT)

    # Helper apply function
    def _apply_for_selected(listbox, selected_list, target_map, var, cast_fn, label):
        i = listbox.curselection()
        # If selection appears empty (focus change), use last selected index
        if not i:
            idx = last_selected_idx.get('idx', -1)
            if idx is None or idx < 0 or idx >= len(selected_list):
                messagebox.showinfo(label, f'Select a device in the right list to set its {label.lower()}.')
                return
            # restore selection visually
            try:
                listbox.selection_clear(0, tk.END)
                listbox.selection_set(idx)
            except Exception:
                pass
        else:
            idx = i[0]
        did = selected_list[idx]['id']
        try:
            val = cast_fn(var.get())
        except Exception:
            messagebox.showerror(label, f'{label} must be a valid {cast_fn.__name__}.')
            return
        target_map[did] = val
        # keep selection after setting
        try:
            listbox.selection_clear(0, tk.END)
            listbox.selection_set(idx)
        except Exception:
            pass

    def apply_colors():
        i = sel.curselection()
        if not i:
            idx = last_selected_idx.get('idx', -1)
            if idx is None or idx < 0 or idx >= len(initial_selected):
                messagebox.showinfo('Colors', 'Select a device in the right list to set its colors.')
                return
            try:
                sel.selection_clear(0, tk.END)
                sel.selection_set(idx)
            except Exception:
                pass
        else:
            idx = i[0]
        did = initial_selected[idx]['id']
        colors_map[did] = {'low': color_low_var.get(), 'mid': color_mid_var.get(), 'high': color_high_var.get()}

    ttk.Button(edit, text='Apply colors', command=apply_colors).pack(anchor='w', pady=4)

    def on_save():
        ordered_ids = [d['id'] for d in initial_selected]
        ordered_names = [d['name'] for d in initial_selected]
        # Build ordered device settings
        ordered_devices = []
        for eid, nm in zip(ordered_ids, ordered_names):
            dev = {
                'id': eid,
                'name': nm,
                'gain': gains_map.get(eid, 1.0),
                'curve': curve_map.get(eid, 1.0),
                'width': width_map.get(eid, 0),
                'colors': colors_map.get(eid, {})
            }
            ordered_devices.append(dev)
        ok = save_config(ordered_devices)
        if not ok:
            messagebox.showwarning('Save', 'Failed to save configuration file.')
        # Apply immediately
        restart_worker(ordered_ids, ordered_devices)
        root.destroy()
        # Uninitialize COM for this UI thread if we initialized it
        try:
            if 'coinit' in locals() and coinit:
                comtypes.CoUninitialize()
        except Exception:
            pass

    def on_cancel():
        root.destroy()
        # Uninitialize COM for this UI thread if we initialized it
        try:
            if 'coinit' in locals() and coinit:
                comtypes.CoUninitialize()
        except Exception:
            pass

    ttk.Button(bottom, text='Save', command=on_save).pack(side=tk.RIGHT, padx=6)
    ttk.Button(bottom, text='Cancel', command=on_cancel).pack(side=tk.RIGHT)

    # Select first item to show its gain
    if sel.size() > 0:
        sel.selection_set(0)
        on_sel_change()

    root.protocol('WM_DELETE_WINDOW', on_cancel)
    root.mainloop()


def on_settings(icon, item):
    threading.Thread(target=open_settings_window, daemon=True).start()


def _show_about_dialog():
    root = None
    top = None
    try:
        # Create hidden root
        root = tk.Tk()
        root.withdraw()

        # Build about text
        text_content = (
            'VU Meter\n\n'
            'A simple Windows system tray VU meter using Pycaw and Pystray.\n\n'
            f'Config: {CONFIG_PATH}\n'
            'Author: Matija Arh (dot in between and google domain)\n'
        )

        # Create a Toplevel window to allow selectable/copyable text
        top = tk.Toplevel(root)
        top.title('About VU Meter')
        try:
            top.attributes('-topmost', True)
        except Exception:
            pass
        top.geometry('520x220')
        try:
            top.resizable(True, True)
        except Exception:
            pass

        # Frame for padding
        container = ttk.Frame(top, padding=8)
        container.pack(fill=tk.BOTH, expand=True)

        # Scrollable, selectable text (read-only)
        text_widget = tk.Text(container, wrap='word', height=8, width=60)
        text_widget.pack(fill=tk.BOTH, expand=True)
        text_widget.insert('1.0', text_content)
        # Make read-only but keep selection/copy; use disabled state after binding
        text_widget.config(state='disabled')

        # Enable Ctrl+A to select all and Ctrl+C to copy
        def enable_copy_bindings(widget):
            def select_all(event=None):
                try:
                    widget.config(state='normal')
                    widget.tag_add('sel', '1.0', 'end-1c')
                finally:
                    widget.config(state='disabled')
                return 'break'

            def copy(event=None):
                try:
                    sel = widget.selection_get()
                except Exception:
                    # If no selection, copy all
                    try:
                        sel = text_content
                    except Exception:
                        sel = ''
                try:
                    widget.clipboard_clear()
                    widget.clipboard_append(sel)
                except Exception:
                    pass
                return 'break'

            widget.bind('<Control-a>', select_all)
            widget.bind('<Control-A>', select_all)
            widget.bind('<Control-c>', copy)
            widget.bind('<Control-C>', copy)

            # Right-click context menu with Copy and Select All
            menu = tk.Menu(widget, tearoff=False)
            def do_copy():
                copy()
            def do_select_all():
                select_all()
            menu.add_command(label='Copy', command=do_copy)
            menu.add_command(label='Select All', command=do_select_all)
            def show_menu(event):
                try:
                    menu.tk_popup(event.x_root, event.y_root)
                finally:
                    try:
                        menu.grab_release()
                    except Exception:
                        pass
            widget.bind('<Button-3>', show_menu)  # Right-click on Windows

        enable_copy_bindings(text_widget)

        # Close button
        btn_frame = ttk.Frame(container)
        btn_frame.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(btn_frame, text='Close', command=top.destroy).pack(side=tk.RIGHT)

        # Bind ESC to close
        top.bind('<Escape>', lambda e: top.destroy())

        # Focus the text widget for immediate Ctrl+C
        try:
            text_widget.focus_set()
        except Exception:
            pass

        # Center the window roughly
        try:
            top.update_idletasks()
            w = top.winfo_width(); h = top.winfo_height()
            sw = top.winfo_screenwidth(); sh = top.winfo_screenheight()
            x = max(0, (sw - w) // 2); y = max(0, (sh - h) // 2)
            top.geometry(f"{w}x{h}+{x}+{y}")
        except Exception:
            pass

        # Run a small local event loop
        top.protocol('WM_DELETE_WINDOW', top.destroy)
        top.mainloop()
    except Exception:
        # As a last resort, fall back to a simple messagebox
        try:
            messagebox.showinfo('About VU Meter', text_content)
        except Exception:
            pass
    finally:
        # Ensure windows are destroyed
        try:
            if top is not None:
                top.destroy()
        except Exception:
            pass
        try:
            if root is not None:
                root.destroy()
        except Exception:
            pass


def on_about(icon, item):
    threading.Thread(target=_show_about_dialog, daemon=True).start()

# Create initial icon image and tray menu
initial_img = Image.new('RGB', (32, 32), (0, 0, 0))
menu = pystray.Menu(
    pystray.MenuItem('Settings…', on_settings),
    pystray.MenuItem('About', on_about),
    pystray.Menu.SEPARATOR,
    pystray.MenuItem('Exit', on_exit)
)
icon = pystray.Icon('VU Meter', icon=initial_img, title='VU Meter', menu=menu)

start_worker()
icon.run()
