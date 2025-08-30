# VU Tray (Windows System Tray VU meter)

A lightweight Windows system tray VU meter that visualizes the peak audio level of one or more audio endpoint devices using:

- pycaw (Windows Core Audio) for device/meter access
- pystray for the tray icon and menu
- Pillow (PIL) to render the bar(s)
- Tkinter for a simple Settings UI

The tray icon animates in real time, showing one vertical bar per selected device. Bar color changes with level (low/mid/high), and you can tune per-device gain, response curve, width in the icon, and colors.


## Features
- Multiple devices visualized simultaneously (one bar per device)
- Per-device settings:
  - Gain (amplify/attenuate the meter)
  - Curve (non-linear display curve exponent 1/f)
  - Width (bar width in the 32×32 tray icon; 0 = auto)
  - Colors (hex RGB for low/mid/high segments)
- Device order controls bar order
- Persistent configuration in a JSON file
- Simple Settings window and About dialog
- CLI helper to list devices or start with specific devices/gains


## Requirements
- Windows 10/11
- Python 3.9+ (recommended)
- Packages:
  - pycaw
  - comtypes
  - pystray
  - pillow

Install deps:

```
pip install pycaw comtypes pystray pillow
```


## Getting Started
1. Clone or download this repository.
2. Install the dependencies (see above).
3. Run the app:

```
python main.py
```

The app starts minimized to the system tray with a black square icon that will animate as audio plays. Right‑click the icon for the menu.


## Usage
- Right‑click tray icon → Settings…
  - Choose available devices, set their order (Up/Down), and edit per‑device:
    - Gain (float, e.g., 1.0)
    - Curve f (float > 0; the display uses level^(1/f))
    - Width px (integer; 0 = auto split)
    - Colors low/mid/high (hex like #00FF00)
  - Click “Apply colors” for the selected device, then Save.
- Right‑click tray icon → About to see basic info.
- Right‑click tray icon → Exit to quit.

The current order determines the left‑to‑right bar order in the tray icon.


## Command Line Options
- List devices and exit:

```
python main.py --list-devices
```

This prints device names with indices and their stable endpoint IDs.

- Start and explicitly choose devices by index or name substring:

```
python main.py --devices 0 1
python main.py --devices "Speakers" "Headphones"
```

- Optionally set initial per‑device gains (in the given order):

```
python main.py --devices 0 1 --gains 1.2 0.8
```

If no devices are provided via CLI, the app tries to load them from the configuration; if none are saved yet, it falls back to the system default render device.


## Configuration
The app stores settings at:

- %APPDATA%\VU_Meter\config.json (e.g., C:\Users\<you>\AppData\Roaming\VU_Meter\config.json)

Example structure (simplified):

```
{
  "devices": [
    {
      "id": "{endpoint-id}",
      "name": "Speakers (Realtek...)",
      "gain": 1.0,
      "curve": 1.0,
      "width": 0,
      "colors": { "low": "#00FF00", "mid": "#FFFF00", "high": "#FF0000" }
    }
  ]
}
```

Notes:
- “id” refers to the device endpoint ID; it’s stable across sessions.
- Width 0 means “auto”: the total 32px width is split among bars, with any remainder added to the first.
- Colors support either hex (e.g., #RRGGBB) or tuple-like values when read from config.


## How It Works
- A worker thread reads IAudioMeterInformation::GetPeakValue for each selected device about every 50 ms.
- Levels are scaled by per-device gain, clamped to [0..1], then passed to an icon renderer that paints a 32×32 RGB image.
- Color selection per bar is based on displayed level: below 0.8 = low, 0.8–0.9 = mid, above 0.9 = high.
- The tray icon is updated with pystray.


## Troubleshooting
- No devices listed / empty list:
  - Ensure audio devices are enabled and drivers installed.
  - Run a console as the same user session; pycaw enumerates current-session endpoints.
- The icon doesn’t animate:
  - Verify audio is playing on the selected device(s).
  - Increase gain slightly in Settings if the signal is low.
- Tkinter window doesn’t show:
  - Some environments restrict GUI on server editions or when running as a service. Run as a normal desktop user.
- Python errors about comtypes/pycaw:
  - Reinstall dependencies and ensure Python is 64-bit if your system is 64-bit.


## Development Notes
- Main entry point: main.py
- Core pieces:
  - create_multi_icon(levels, settings): draws the tray icon image
  - update(...): worker thread that polls device meters and updates the icon
  - open_settings_window(): Tkinter UI for device selection and per-device parameters
  - Config helpers: load_config, save_config, list_all_devices


## Roadmap Ideas
- Optional tooltip with current dB / level values
- Customizable thresholds for color transitions
- Option to use RMS instead of peak (if available)
- Packaging with PyInstaller for a single-file executable


## License
Specify your license here (e.g., MIT). If you’re unsure, consider adding an SPDX identifier and a LICENSE file.


## Acknowledgements
- pycaw for Windows Core Audio access
- pystray for tray icon support
- Pillow for drawing
