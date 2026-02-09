#!/usr/bin/env python3
import sys
import re

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
    if len(sys.argv) < 2:
        print("Usage: python3 find_missing_msgstr.py <path_to_po_file>")
        sys.exit(1)
    
    po_file = sys.argv[1]
    print(f"Checking for missing translations in {po_file}...")
    
    missing_count = find_missing_translations(po_file)
    
    if missing_count > 0:
        print("\nFailure: Missing translations found.")
        sys.exit(1)
    else:
        print("\nSuccess: No missing translations found.")
        sys.exit(0)
