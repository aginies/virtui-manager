import json
import urllib.request
import sys

# Packages list
packages = [
    "certifi",
    "cffi",
    "charset-normalizer",
    "cryptography",
    "idna",
    "jwcrypto",
    "linkify-it-py",
    "markdown-it-py",
    "mdit-py-plugins",
    "mdurl",
    "numpy",
    "platformdirs",
    "pycparser",
    "pygments",
    "pyparsing",
    "PyYAML",
    "redis",
    "requests",
    "rich",
    "setuptools",
    "six",
    "textual",
    "typing-extensions",
    "uc-micro-py",
    "urllib3",
    "websockify",
    "wheel",
]

modules = []

def get_best_wheel(pkg_name, data):
    # Priority list for Python 3.12 on Linux x86_64
    priorities = [
        "cp312-cp312-manylinux",
        "cp312-abi3-manylinux",
        "cp311-abi3-manylinux", # abi3 is compatible
        "abi3-manylinux",
        "py3-none-any",
        "py2.py3-none-any"
    ]
    
    urls = data.get('urls', [])
    
    # Filter for wheels only
    wheels = [u for u in urls if u['packagetype'] == 'bdist_wheel']
    
    # Pre-filter for architecture
    valid_wheels = []
    for w in wheels:
        fn = w['filename']
        if "manylinux" in fn or "abi3" in fn:
            if "x86_64" in fn:
                valid_wheels.append(w)
        elif "any" in fn:
            valid_wheels.append(w)
            
    wheels = valid_wheels
    
    for priority in priorities:
        for w in wheels:
            fn = w['filename']
            if priority in fn:
                return w
                
    # Fallback: try just 'cp312' if not found above
    for w in wheels:
        if "cp312" in w['filename'] and "manylinux" in w['filename']:
            return w

    return None

sources = []

for pkg in packages:
    try:
        # Get latest version metadata
        url = f"https://pypi.org/pypi/{pkg}/json"
        with urllib.request.urlopen(url) as r:
            data = json.load(r)
            
        wheel = get_best_wheel(pkg, data)
        
        if not wheel:
            print(f"Error: Could not find compatible wheel for {pkg}", file=sys.stderr)
            continue
            
        sources.append({
            "type": "file",
            "url": wheel['url'],
            "sha256": wheel['digests']['sha256']
        })
        print(f"Resolved {pkg} -> {wheel['filename']}", file=sys.stderr)
            
    except Exception as e:
        print(f"Error fetching {pkg}: {e}", file=sys.stderr)

# Single module definition
module = {
    "name": "python-dependencies",
    "buildsystem": "simple",
    "build-commands": [
        "pip3 install --prefix=/app --no-deps --no-index --ignore-installed --find-links . *.whl"
    ],
    "sources": sources
}

import yaml
print(yaml.dump(module))
