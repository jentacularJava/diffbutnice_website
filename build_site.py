import os
import yaml
import shutil

# Paths
COMICS_DIR = 'comic_files'
SITE_DIR = 'site'
METADATA_FILE = 'comics.yaml'
JS_OUT = os.path.join(SITE_DIR, 'comics.js')
IMG_OUT_DIR = os.path.join(SITE_DIR, COMICS_DIR)

def get_all_comics():
    # Try to load metadata if available
    try:
        with open(METADATA_FILE, 'r') as f:
            metadata = yaml.safe_load(f)
    except Exception:
        metadata = []
    # Build a dict for quick lookup
    meta_dict = {entry.get('filename'): entry for entry in metadata if entry.get('filename')} if metadata else {}
    # List all image files in comic_files
    files = [f for f in os.listdir(COMICS_DIR) if f.lower().endswith(('.jpg', '.jpeg', '.png', '.gif'))]
    comics = []
    for filename in sorted(files):
        entry = meta_dict.get(filename, {})
        title = entry.get('title', '')
        caption = entry.get('caption', '')
        comics.append({
            'filename': filename,
            'title': title,
            'caption': caption
        })
    return comics

def write_comics_js(comics):
    with open(JS_OUT, 'w') as f:
        f.write('// Auto-generated comic data for SPA\n')
        f.write('const comics = [\n')
        for comic in comics:
            f.write('  {\n')
            f.write(f"    title: {repr(comic['title'])},\n")
            f.write(f"    filename: {repr(comic['filename'])},\n")
            f.write(f"    caption: {repr(comic['caption'])}\n")
            f.write('  },\n')
        f.write('];\n')

def copy_images(comics):
    os.makedirs(IMG_OUT_DIR, exist_ok=True)
    for comic in comics:
        src = os.path.join(COMICS_DIR, comic['filename'])
        dst = os.path.join(IMG_OUT_DIR, comic['filename'])
        if not os.path.exists(dst):
            shutil.copy2(src, dst)

def main():
    comics = get_all_comics()
    write_comics_js(comics)
    copy_images(comics)
    print('SPA comic data and images updated. All images in comic_files are now included.')

if __name__ == '__main__':
    main()
