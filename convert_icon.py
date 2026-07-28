#!/usr/bin/env python3
"""
TELELLUC ICON CONVERTER - Convert PNG to ICO format

Convert settings.png to telelluc.ico with multiple resolutions

COMMIT GUIDELINES:
==================
Format: git commit -m "vX.X.X

Description of changes

Changes:
- Specific change 1
- Specific change 2

Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>"

USAGE:
python convert_icon.py

This creates telelluc.ico from settings.png
Include telelluc.ico in commits when icon changes
"""

from PIL import Image
import os

def convert_to_ico():
    """Convert settings.png to ICO format"""

    # Open the settings.png image
    png_path = 'settings.png'

    if not os.path.exists(png_path):
        print(f"❌ Error: {png_path} not found")
        return

    # Open original image
    original = Image.open(png_path).convert('RGBA')

    # Create images at different sizes
    sizes = [16, 32, 48, 64, 128, 256]
    images = []

    for size in sizes:
        # Resize image maintaining aspect ratio
        resized = original.resize((size, size), Image.Resampling.LANCZOS)
        images.append(resized)

    # Save as ICO file
    ico_path = 'telelluc.ico'

    try:
        # Save with all images embedded (from largest to smallest)
        images[-1].save(
            ico_path,
            format='ICO',
            append_images=images[:-1][::-1]
        )

        file_size = os.path.getsize(ico_path)
        print(f"✅ Icon converted successfully!")
        print(f"   Source: {png_path}")
        print(f"   Output: {ico_path}")
        print(f"   File size: {file_size:,} bytes")
        print(f"   Resolutions: {sizes}")
        print(f"   Ready to use with PyInstaller --icon {ico_path}")

    except Exception as e:
        print(f"❌ Error: {e}")
        return

if __name__ == '__main__':
    convert_to_ico()
