#!/usr/bin/env python3
import sys
import re
import glob

def find_missing_translations(po_file_path):
    """
    Parses a .po file and finds entries where msgstr is empty ("").
    """
    try:
        with open(po_file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except FileNotFoundError:
        print(f"Error: File not found at {po_file_path}")
        return

    missing_count = 0
    current_msgid = None
    current_msgstr = None
    line_number = 0
    
    # Simple state machine
    # 0: looking for msgid
    # 1: inside msgid (handling multiline)
    # 2: looking for msgstr
    # 3: inside msgstr (handling multiline)
    state = 0
    
    msgid_buffer = []
    
    for i, line in enumerate(lines):
        line = line.strip()
        
        if line.startswith('msgid "'):
            state = 1
            msgid_buffer = [line[7:-1]] # remove msgid " and trailing "
        elif line.startswith('"') and state == 1:
            msgid_buffer.append(line[1:-1])
        elif line.startswith('msgstr "'):
            state = 3
            current_msgstr = line[8:-1]
            
            # Check if it's empty and we have a valid msgid
            full_msgid = "".join(msgid_buffer)
            if current_msgstr == "" and full_msgid != "":
                # Check next line to see if it continues (multiline empty string)
                # But usually empty translation is just msgstr ""
                is_actually_empty = True
                if i + 1 < len(lines):
                    next_line = lines[i+1].strip()
                    if next_line.startswith('"'):
                        is_actually_empty = False
                
                if is_actually_empty:
                    print(f"Missing translation at line {i+1}:")
                    print(f"  msgid \"{full_msgid}\"")
                    missing_count += 1
            
            msgid_buffer = [] # Reset for next
            state = 0 # Reset state

    print(f"\nTotal missing translations: {missing_count}")
    return missing_count

if __name__ == "__main__":
    base_path = "src/vmanager/locale"
    po_files = glob.glob(f"{base_path}/**/*.po", recursive=True)
    
    if not po_files:
        print(f"No .po files found in {base_path}")
        sys.exit(0)

    total_missing_count = 0
    
    for po_file in po_files:
        print(f"Checking for missing translations in {po_file}...")
        missing_count = find_missing_translations(po_file)
        if missing_count is not None:
            total_missing_count += missing_count
    
    if total_missing_count > 0:
        print(f"\nFailure: Total missing translations across all files: {total_missing_count}.")
        sys.exit(1)
    else:
        print("\nSuccess: No missing translations found in any .po file.")
        sys.exit(0)
