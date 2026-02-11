#!/usr/bin/env python3
import os
import re

def update_media_gallery():
    image_dir = "docs/manual/images"
    html_file = "docs/media.html"
    
    # Extensions to include
    valid_extensions = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg')
    
    # Files to exclude (logos, icons, etc.)
    exclude_patterns = ['logo', 'icon']
    
    print(f"Scanning {image_dir} for images...")
    
    images = []
    if not os.path.exists(image_dir):
        print(f"Error: Directory {image_dir} not found.")
        return

    for filename in sorted(os.listdir(image_dir)):
        if filename.lower().endswith(valid_extensions):
            # Check if filename contains any exclude patterns
            if any(pattern in filename.lower() for pattern in exclude_patterns):
                continue
            images.append(filename)
    
    print(f"Found {len(images)} images.")

    if not os.path.exists(html_file):
        print(f"Error: {html_file} not found.")
        return

    with open(html_file, 'r') as f:
        content = f.read()

    # Create the JS array string
    js_array = "const images = [\n"
    for i, img in enumerate(images):
        comma = "," if i < len(images) - 1 else ""
        js_array += f"            '{img}'{comma}\n"
    js_array += "        ];"

    # Regex to find the images array in media.html
    # It looks for 'const images = [' and everything until '];'
    pattern = r"const images = \[.*?\].*?;"
    
    if re.search(pattern, content, re.DOTALL):
        new_content = re.sub(pattern, js_array, content, flags=re.DOTALL)
        with open(html_file, 'w') as f:
            f.write(new_content)
        print(f"Successfully updated {html_file}")
    else:
        print("Error: Could not find the 'const images' array in media.html")

if __name__ == "__main__":
    update_media_gallery()
