#!/usr/bin/env python3
"""
Create a professional icon for telelluc Windows Agent Service EXE
"""

from PIL import Image, ImageDraw, ImageFont
import os

def create_icon():
    """Create a professional Windows Service icon - similar to system services"""

    # Create image with multiple sizes for ICO format
    sizes = [16, 32, 48, 64, 128, 256]
    images = []

    for size in sizes:
        # Create new image with solid background (Windows Service style)
        img = Image.new('RGBA', (size, size), (240, 240, 240))  # Light gray
        draw = ImageDraw.Draw(img)

        # Draw dark background square (Windows service style)
        margin = int(size * 0.1)
        bg_color = (45, 45, 48)  # Dark gray like Windows services
        draw.rectangle(
            [(margin, margin), (size - margin, size - margin)],
            fill=bg_color,
            outline=(100, 100, 100),
            width=max(1, int(size * 0.03))
        )

        # Draw gear/service symbol (centered)
        center_x = size // 2
        center_y = size // 2
        gear_radius = int(size * 0.25)

        # Main circle
        circle_margin = int(size * 0.15)
        draw.ellipse(
            [(circle_margin, circle_margin),
             (size - circle_margin, size - circle_margin)],
            fill=(70, 130, 180),  # Steel blue
            outline=(255, 255, 255),  # White border
            width=max(1, int(size * 0.04))
        )

        # Inner gear-like symbol (simplified)
        inner_margin = int(size * 0.25)
        draw.ellipse(
            [(inner_margin, inner_margin),
             (size - inner_margin, size - inner_margin)],
            fill=(70, 130, 180),
            outline=(255, 255, 255),
            width=max(1, int(size * 0.025))
        )

        # Draw small white dots around the circle (service indicator)
        tooth_count = 8
        import math
        for i in range(tooth_count):
            angle = (2 * math.pi * i) / tooth_count
            dot_x = center_x + int(gear_radius * math.cos(angle))
            dot_y = center_y + int(gear_radius * math.sin(angle))
            dot_size = max(1, int(size * 0.04))
            draw.ellipse(
                [(dot_x - dot_size, dot_y - dot_size),
                 (dot_x + dot_size, dot_y + dot_size)],
                fill=(255, 255, 255)
            )

        # Add small green indicator (running status)
        indicator_size = int(size * 0.15)
        indicator_x = size - indicator_size - int(size * 0.05)
        indicator_y = size - indicator_size - int(size * 0.05)
        draw.ellipse(
            [(indicator_x, indicator_y),
             (indicator_x + indicator_size, indicator_y + indicator_size)],
            fill=(0, 200, 100),  # Green - running
            outline=(0, 150, 75),
            width=max(1, int(size * 0.02))
        )

        images.append(img)

    # Save as ICO file - use largest image (256x256) and append others
    ico_path = 'telelluc.ico'

    try:
        # Save with all images embedded
        images[-1].save(
            ico_path,
            format='ICO',
            append_images=images[:-1][::-1]  # Include all other sizes
        )
    except Exception as e:
        # Fallback: just save the largest image
        print(f"Note: {e}")
        images[-1].save(ico_path, format='ICO')

    import os
    file_size = os.path.getsize(ico_path)
    print(f"✅ Icon created: {ico_path}")
    print(f"   File size: {file_size:,} bytes")
    print(f"   Sizes included: {sizes}")
    print(f"   Style: Windows Service (Steel Blue with Green running indicator)")
    print(f"   Ready to use with PyInstaller")

    return ico_path

if __name__ == '__main__':
    create_icon()
