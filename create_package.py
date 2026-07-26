import os
import zipfile

# Configuration
# Use the directory containing this script as source
SOURCE_DIR = os.path.dirname(os.path.abspath(__file__))
# Save to the root of the addon or a subfolder
DEST_PATH = os.path.join(SOURCE_DIR, "Anki_EDN_Lier_les_cartes.ankiaddon")

EXCLUDES = [
    "__pycache__",
    "meta.json",      # Exclude to prevent overwriting user config on update
    "tests",
    "debug_",
    "manual_",
    "INSTRUCTIONS_DEBUG",
    ".git",
    ".gitignore",
    ".vscode",
    ".idea",
    ".ankiaddon",     # Prevent recursive zipping
    ".log"
]

def should_exclude(rel_path):
    parts = rel_path.split(os.sep)
    for part in parts:
        for exclude in EXCLUDES:
            if exclude in part or (exclude.startswith("*") and part.endswith(exclude.replace("*", ""))):
                return True
    return False

def create_addon_package():
    print(f"Packaging addon from: {SOURCE_DIR}")
    print(f"Destination: {DEST_PATH}")
    
    with zipfile.ZipFile(DEST_PATH, 'w', zipfile.ZIP_DEFLATED) as zipf:
        # Walk through the directory
        for root, dirs, files in os.walk(SOURCE_DIR):
            # Modify dirs in-place to skip excluded directories during walk
            dirs[:] = [d for d in dirs if not should_exclude(d)]
            
            for file in files:
                if should_exclude(file):
                    continue
                
                abs_path = os.path.join(root, file)
                rel_path = os.path.relpath(abs_path, SOURCE_DIR)
                
                print(f"Adding: {rel_path}")
                zipf.write(abs_path, rel_path)

    print("\nPackage created successfully!")

if __name__ == "__main__":
    try:
        create_addon_package()
    except Exception as e:
        print(f"Error: {e}")
