import os
import shutil
import logging

log = logging.getLogger("mkdocs")

def on_config(config):
    src_dir = os.path.join(os.getcwd(), "src", "vmanager", "appdocs")
    dest_dir = os.path.join(os.getcwd(), "documentation", "manual", "appdocs")

    if not os.path.exists(dest_dir):
        os.makedirs(dest_dir)

    log.info(f"Copying external docs from {src_dir} to {dest_dir}")
    
    for filename in os.listdir(src_dir):
        if filename.endswith(".md"):
            shutil.copy(os.path.join(src_dir, filename), os.path.join(dest_dir, filename))
    return config

def on_post_build(config, **kwargs):
    dest_dir = os.path.join(os.getcwd(), "documentation", "manual", "appdocs")
    if os.path.exists(dest_dir):
        log.info(f"Cleaning up {dest_dir}")
        shutil.rmtree(dest_dir)
