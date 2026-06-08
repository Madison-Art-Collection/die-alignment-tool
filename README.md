# Die Alignment Tool

A desktop tool for visually comparing two coin photographs to assess whether they share a die. It overlays one image on top of the other with adjustable scale, rotation, and transparency, and supports both automatic and manual point-based alignment. Optional background segmentation is available for coins photographed on dark backgrounds.

## Requirements

- Python 3.10 or later
- `tkinter` (included in the standard library; on Linux you may need `python3-tk` from your package manager)

## Installation

```bash
# Create and activate a virtual environment (recommended)
python3 -m venv .venv
source .venv/bin/activate        # macOS / Linux
.venv\Scripts\activate           # Windows

# Install dependencies
pip install -r requirements.txt
```

## Usage

```bash
# Open with file dialogs (load both coins from within the UI)
python die_overlay.py

# Pre-load one or both images from the command line
python die_overlay.py --coin-a path/to/coin_a.jpg --coin-b path/to/coin_b.jpg
```

When both coins are loaded (either via CLI or the Load buttons), the tool automatically scales and centers Coin B over Coin A as a starting position.

## Workflow

1. Click **Load Coin A…** to select the reference image. This fills the canvas, centered with aspect ratio preserved.
2. Click **Load Coin B…** to select the comparison image. The tool auto-fits it over Coin A with aspect ratio preserved.
3. Use the controls to align the coins:
   - **Drag** the canvas to pan Coin B
   - **Scale / Rotation / Offset** spin boxes for fine adjustments (scale applies uniformly to preserve aspect ratio)
   - **Transparency** slider to blend between the two coins
   - **Manual Align** for precise point-based alignment (see below)
4. Toggle **Flip coin B** if the coin was struck from the same die but photographed in the opposite orientation.
5. Toggle **Swap base/overlay** to reverse which coin is the transparent layer.
6. Toggle **Remove background (segmentation)** if your coins are photographed on dark backgrounds and you want the background removed. Off by default.

## Controls reference

| Control | Action |
|---|---|
| Drag canvas | Pan Coin B |
| Arrow keys | Nudge Coin B 1 px |
| Shift + arrow | Nudge Coin B 5 px |
| `=` / `-` | Scale ±0.001 |
| `+` / `_` | Scale ±0.01 |
| **Auto-fit** button | Re-center and rescale Coin B to fill canvas |
| **Reset transform** button | Restore default scale/position/rotation |

**Performance note:** The transparency slider is optimized for smooth interaction with high-resolution images. Image transformations are cached and only recomputed when scale, rotation, or position changes.

## Manual alignment

Click **Manual Align (4+ pts)** to open the alignment dialog. The left panel shows Coin A and the right panel shows Coin B (both shown as original unsegmented images for clarity).

1. Click a distinctive feature on **Coin A** (left panel) — a legend letter, die break, symbol, etc.
2. Click the matching feature on **Coin B** (right panel).
3. Repeat for at least 4 pairs. Points are color-coded by pair number.
4. Click **Compute alignment** to apply the transform. The tool uses a robust least-median-of-squares estimator, so one slightly off point among four will not ruin the result.

Tips for good alignment:
- Spread points across the full coin face — avoid clustering.
- Use sharp, unambiguous features: letters, symbol tips, die breaks.
- Add more pairs (up to 8) if the first compute looks off.
- Use **Undo** to remove the last-placed point, or **Reset** to start over.

## Examples

Successful die matches identified using this tool:

- **[2024.2.33](https://jmu.emuseum.com/objects/8843/didrachm-of-velia?ctx=a079862b062285acfb9efa80b6d79c0fe053465a&idx=0)** aligns with **Williams 413** 
- **2024.2.34** aligns with **Williams 577**

These examples demonstrate how the tool can identify coins struck from the same die by precisely overlaying and comparing fine details in the die surface.

## Image format support

JPEG, PNG, TIFF. 

### Background segmentation (optional)

By default, images are displayed as-is. If your coins are photographed on dark felt/velvet backgrounds, you can enable the **"Remove background (segmentation)"** checkbox to automatically remove the background using Otsu thresholding. This feature:
- Works best with coins on uniformly dark backgrounds
- Is **off by default** to ensure reliable transparency slider behavior
- Can be toggled on/off at any time to reprocess Coin B

**Note:** If segmentation produces artifacts or removes parts of the coin, simply uncheck the option to use the full image.
