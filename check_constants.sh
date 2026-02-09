#!/bin/bash

# Path to the constants file
CONSTANTS_FILE="src/vmanager/constants.py"

# Check if the constants file exists
if [ ! -f "$CONSTANTS_FILE" ]; then
    echo "Error: Constants file '$CONSTANTS_FILE' not found."
    exit 1
fi

# Directory containing .py files to check
SEARCH_DIR="src"

# Extract class names defined in constants.py
CLASS_NAMES=$(grep "^class " "$CONSTANTS_FILE" | awk '{print $2}' | cut -d: -f1)

# Function to get variables for a specific class from constants.py
get_class_vars() {
    local class_name="$1"
    # Python script to extract variables from a class in a file
    python3 -c "
import ast
import sys

def get_class_vars(filename, class_name):
    with open(filename, 'r') as f:
        tree = ast.parse(f.read())
    
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            vars = []
            for item in node.body:
                if isinstance(item, ast.Assign):
                    for target in item.targets:
                        if isinstance(target, ast.Name):
                            vars.append(target.id)
            print(' '.join(vars))
            return

get_class_vars('$CONSTANTS_FILE', '$class_name')
"
}

# Check usage in .py files
echo "Checking usage of constants in $SEARCH_DIR..."

ERROR_FOUND=0

for class_name in $CLASS_NAMES; do
    # Get all variables defined in this class
    VARS=$(get_class_vars "$class_name")
    
    # Find all usages of ClassName.VARIABLE in .py files
    # We look for patterns like ClassName.VARIABLE
    
    # Get a list of files that use this class
    FILES_USING_CLASS=$(grep -rl "$class_name" "$SEARCH_DIR" --include="*.py" | grep -v "$CONSTANTS_FILE")
    
    for file in $FILES_USING_CLASS; do
        # Extract usages of ClassName.SOMETHING
        USAGES=$(grep -o "$class_name\.[A-Z0-9_]*" "$file" | cut -d. -f2)
        
        for usage in $USAGES; do
            # Check if the used variable exists in the class definition
            found=0
            for var in $VARS; do
                if [ "$usage" == "$var" ]; then
                    found=1
                    break
                fi
            done
            
            if [ $found -eq 0 ]; then
                echo "Error: File '$file' uses '$class_name.$usage' which is NOT defined in $CONSTANTS_FILE"
                ERROR_FOUND=1
            fi
        done
    done
done

if [ $ERROR_FOUND -eq 0 ]; then
    echo "Success: All constant usages are valid."
    exit 0
else
    echo "Failure: Invalid constant usages found."
    exit 1
fi
