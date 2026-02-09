#!/bin/bash

# Determine the directory where the script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

# Go to that directory
cd "$SCRIPT_DIR"

LOCALES_DIR="locale"
DOMAIN="virtui-manager"
POT_FILE="$LOCALES_DIR/$DOMAIN.pot"
PACKAGE_NAME="VirtUI Manager"
PACKAGE_VERSION=""
extract_version


function extract_version() {
    # Try to grep version from constants.py
    local ver=$(grep 'version = "' constants.py | cut -d'"' -f2)
    if [ ! -z "$ver" ]; then
        PACKAGE_VERSION="$ver"
    fi
}

function generate_pot() {
    echo "--- Generating POT file ---"
    extract_version
    mkdir -p "$LOCALES_DIR"
    
    echo "Extracting messages from constants.py to $POT_FILE..."
    xgettext --language=Python --keyword=_ --from-code=UTF-8 \
             --package-name="$PACKAGE_NAME" \
             --package-version="$PACKAGE_VERSION" \
             --copyright-holder="VirtUI Manager" \
             --msgid-bugs-address="https://github.com/aginies/virtui-manager/issues" \
             --output="$POT_FILE" constants.py
    
    # Update charset in header to UTF-8
    if [ -f "$POT_FILE" ]; then
        sed -i 's/charset=CHARSET/charset=UTF-8/g' "$POT_FILE"
        echo "POT file generated successfully."
    else
        echo "Error: Failed to generate POT file."
        exit 1
    fi
}

function update_po() {
    echo "--- Updating PO files ---"
    if [ ! -f "$POT_FILE" ]; then
        echo "POT file not found. Generating it first..."
        generate_pot
    fi

    local found_po=false
    # Find all .po files in subdirectories of locale/
    while read -r po_file; do
        if [ -z "$po_file" ]; then continue; fi
        echo "Updating $po_file..."
        msgmerge --update --backup=none "$po_file" "$POT_FILE"
        found_po=true
    done < <(find "$LOCALES_DIR" -name "$DOMAIN.po")
}

function show_create() {
    echo "To create a new language (e.g. French):"
    echo "  mkdir -p $LOCALES_DIR/fr/LC_MESSAGES"
    echo "  msginit --input=$POT_FILE --output=$LOCALES_DIR/fr/LC_MESSAGES/$DOMAIN.po --locale=fr"
}

function compile_mo() {
    echo "--- Compiling MO files ---"
    local found_po=false
    while read -r po_file; do
        if [ -z "$po_file" ]; then continue; fi
        mo_file="${po_file%.po}.mo"
        echo "Compiling $po_file -> $mo_file"
        msgfmt "$po_file" -o "$mo_file"
        found_po=true
    done < <(find "$LOCALES_DIR" -name "$DOMAIN.po")
    
    if [ "$found_po" = false ]; then
        echo "No .po files found to compile."
    fi
}

function show_help() {
    echo "Usage: $0 {gen-pot|update-po|compile-mo|all}"
    echo ""
    echo "Commands:"
    echo "  gen-pot     Generate the template ($DOMAIN.pot) from constants.py"
    echo "  update-po   Update existing .po files from the .pot template"
    echo "  compile-mo  Compile .po files into .mo binary files"
    echo "  show        Show how to create a new language"
    echo "  all         Run all steps in order"
}

# Check command line arguments
case "$1" in
    gen-pot)
        generate_pot
        ;;
    update-po)
        update_po
        ;;
    compile-mo)
        compile_mo
        ;;
    show)
        show_create
	;;
    all)
        generate_pot
        update_po
        compile_mo
        ;;
    *)
        show_help
        exit 1
        ;;
esac
