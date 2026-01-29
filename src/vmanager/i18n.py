import gettext
import os
import locale
import logging

# Setup path to locales
# This assumes the 'locales' directory is next to this file
LOCALE_DIR = os.path.join(os.path.dirname(__file__), 'locales')
DOMAIN = 'virtui-manager'

_ = lambda s: s

def setup_i18n():
    global _
    try:
        lang, _ = locale.getdefaultlocale()
    except Exception:
        lang = 'en'
        
    if not lang:
        lang = 'en'

    try:
        t = gettext.translation(DOMAIN, LOCALE_DIR, fallback=True)
        _ = t.gettext
        logging.debug(f"i18n initialized for domain '{DOMAIN}' with locale dir '{LOCALE_DIR}'")
    except Exception as e:
        logging.warning(f"Failed to initialize i18n: {e}")
        _ = lambda s: s

import sys
setup_i18n()
