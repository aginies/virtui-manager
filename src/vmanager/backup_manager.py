"""
Backup Manager - Core backup operations for VirtUI Manager

This module provides backup functionality including:
- Multiple backup type support (snapshots, overlays)
- Compression and encryption
- Backup restoration
- Retention-based cleanup
"""

import gzip
import hashlib
import logging
import os
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
import datetime
import json

import libvirt

from enum import Enum
from dataclasses import dataclass


class BackupType(Enum):
    """Supported backup types."""

    SNAPSHOT = "snapshot"
    OVERLAY = "overlay"


@dataclass
class BackupOptions:
    """Options for backup creation."""

    compress: bool = False
    encrypt: bool = False
    quiesce: bool = False


from .storage_manager import create_overlay_volume, delete_volume
from .vm_actions import create_vm_snapshot, delete_vm_snapshot
from .vm_queries import get_vm_snapshots
from .libvirt_utils import _find_vol_by_path


class BackupManager:
    """Advanced backup operations and management."""

    def __init__(self, config_dir: Optional[Path] = None):
        self.config_dir = config_dir or Path.home() / ".config" / "virtui-manager"
        self.backup_metadata_dir = self.config_dir / "backup_metadata"
        self.backup_metadata_dir.mkdir(parents=True, exist_ok=True)

        # Encryption key location
        self.encryption_key_file = self.config_dir / "backup_encryption.key"

    def create_backup(
        self,
        domain: libvirt.virDomain,
        backup_name: str,
        backup_type: BackupType,
        options: BackupOptions,
        server_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a backup with the given options.

        Returns backup metadata including size, checksum, etc.
        """
        start_time = datetime.datetime.now()
        backup_metadata = {
            "name": backup_name,
            "vm_name": domain.name(),
            "server_name": server_name,
            "type": backup_type.value,
            "created_at": start_time.isoformat(),
            "options": {
                "compress": options.compress,
                "encrypt": options.encrypt,
                "quiesce": options.quiesce,
            },
        }

        # Ensure the encryption key exists upfront if encryption is requested
        if options.encrypt:
            self._get_encryption_key()

        try:
            if backup_type == BackupType.SNAPSHOT:
                result = self._create_snapshot_backup(domain, backup_name, options)
            elif backup_type == BackupType.OVERLAY:
                result = self._create_overlay_backup(domain, backup_name, options)
            else:
                raise ValueError(f"Unsupported backup type: {backup_type}")

            backup_metadata.update(result)

            end_time = datetime.datetime.now()
            backup_metadata["completed_at"] = end_time.isoformat()
            backup_metadata["duration_seconds"] = (end_time - start_time).total_seconds()

            self._save_backup_metadata(backup_name, backup_metadata)

            logging.info(f"Backup {backup_name} created successfully")
            return backup_metadata

        except Exception as e:
            backup_metadata["error"] = str(e)
            backup_metadata["completed_at"] = datetime.datetime.now().isoformat()
            self._save_backup_metadata(backup_name, backup_metadata)

            logging.error(f"Backup {backup_name} failed: {e}")
            raise

    def _create_snapshot_backup(
        self, domain: libvirt.virDomain, backup_name: str, options: BackupOptions
    ) -> Dict[str, Any]:
        """Create a VM snapshot backup."""
        description = f"Automated backup created at {datetime.datetime.now().isoformat()}"

        create_vm_snapshot(domain, backup_name, description, options.quiesce)

        snapshots = get_vm_snapshots(domain)
        snapshot_info = next((s for s in snapshots if s["name"] == backup_name), None)

        if not snapshot_info:
            raise Exception(f"Created snapshot {backup_name} not found")

        return {
            "snapshot_info": snapshot_info,
            "size_bytes": 0,  # Snapshots don't have a direct size
            "checksum": None,
            "compressed": False,
            "encrypted": False,
        }

    def _create_overlay_backup(
        self, domain: libvirt.virDomain, backup_name: str, options: BackupOptions
    ) -> Dict[str, Any]:
        """Create an overlay-based backup."""
        conn = domain.connect()

        from .vm_queries import get_vm_disks_info, _get_domain_root

        _, root = _get_domain_root(domain)
        disks = get_vm_disks_info(conn, root)

        if not disks:
            raise Exception("No disks found for overlay backup")

        overlay_paths = []
        backing_paths = {}
        total_size = 0

        try:
            for disk in disks:
                if disk.get("device") != "disk":
                    continue

                disk_path = disk.get("path")
                if not disk_path:
                    continue

                vol, pool = _find_vol_by_path(conn, disk_path)
                if not pool:
                    logging.warning(f"Could not find pool for disk {disk_path}")
                    continue

                overlay_name = f"{backup_name}_{os.path.basename(disk_path)}_overlay.qcow2"
                overlay_vol = create_overlay_volume(pool, overlay_name, disk_path)
                overlay_path = overlay_vol.path()
                overlay_paths.append(overlay_path)
                backing_paths[overlay_path] = disk_path

                _, capacity, allocation = overlay_vol.info()
                total_size += allocation

        except Exception as e:
            # Cleanup any created overlays on failure
            for overlay_path in overlay_paths:
                try:
                    vol, _ = _find_vol_by_path(conn, overlay_path)
                    if vol:
                        delete_volume(vol)
                except Exception:
                    pass
            raise e

        # Apply compression and/or encryption if requested
        if options.compress or options.encrypt:
            processed_paths = []
            processed_size = 0
            checksums = []

            for overlay_path in overlay_paths:
                processed_path, size, checksum = self._process_file(
                    overlay_path, backup_name, options
                )
                processed_paths.append(processed_path)
                processed_size += size
                checksums.append(checksum)

            return {
                "processed_paths": processed_paths,
                "original_paths": overlay_paths,
                "backing_paths": backing_paths,
                "size_bytes": processed_size,
                "checksums": checksums,
                "compressed": options.compress,
                "encrypted": options.encrypt,
            }

        return {
            "overlay_paths": overlay_paths,
            "backing_paths": backing_paths,
            "size_bytes": total_size,
            "checksum": None,
            "compressed": False,
            "encrypted": False,
        }

    def _process_file(
        self, file_path: str, backup_name: str, options: BackupOptions
    ) -> Tuple[str, int, str]:
        """
        Process a file with compression and/or encryption.
        Returns: (processed_path, size, checksum)
        """
        processed_path = file_path

        if options.compress:
            compressed_path = f"{file_path}.gz"
            with open(file_path, "rb") as f_in:
                with gzip.open(compressed_path, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
            processed_path = compressed_path

        if options.encrypt:
            encrypted_path = f"{processed_path}.enc"
            self._encrypt_file(processed_path, encrypted_path)
            if processed_path != file_path:  # Remove intermediate compressed file
                os.remove(processed_path)
            processed_path = encrypted_path

        size = os.path.getsize(processed_path)
        checksum = self._calculate_checksum(processed_path)

        return processed_path, size, checksum

    def _get_encryption_key(self) -> str:
        """Get or generate the encryption key."""
        if self.encryption_key_file.exists():
            with open(self.encryption_key_file, "r") as f:
                return f.read().strip()

        import secrets

        key = secrets.token_hex(32)
        with open(self.encryption_key_file, "w") as f:
            f.write(key)
        os.chmod(self.encryption_key_file, 0o600)
        return key

    def _encrypt_file(self, input_path: str, output_path: str):
        """Encrypt a file using OpenSSL with the stored encryption key."""
        # Ensure the key exists
        self._get_encryption_key()
        try:
            subprocess.run(
                [
                    "openssl",
                    "enc",
                    "-aes-256-cbc",
                    "-salt",
                    "-in",
                    input_path,
                    "-out",
                    output_path,
                    "-kfile",
                    str(self.encryption_key_file),
                ],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as e:
            raise Exception(f"Encryption failed: {e.stderr.decode()}")
        except FileNotFoundError:
            raise Exception("OpenSSL not found - encryption requires openssl to be installed")

    def _calculate_checksum(self, file_path: str) -> str:
        """Calculate SHA256 checksum of a file."""
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                sha256_hash.update(chunk)
        return sha256_hash.hexdigest()

    def _save_backup_metadata(self, backup_name: str, metadata: Dict[str, Any]):
        """Save backup metadata to disk."""
        metadata_file = self.backup_metadata_dir / f"{backup_name}.json"
        with open(metadata_file, "w") as f:
            json.dump(metadata, f, indent=2, default=str)

    def _load_backup_metadata(self, backup_name: str) -> Optional[Dict[str, Any]]:
        """Load backup metadata from disk."""
        metadata_file = self.backup_metadata_dir / f"{backup_name}.json"
        if metadata_file.exists():
            try:
                with open(metadata_file, "r") as f:
                    return json.load(f)
            except Exception as e:
                logging.error(f"Error loading backup metadata for {backup_name}: {e}")
        return None

    def cleanup_old_backups(
        self,
        vm_name: str,
        backup_type: BackupType,
        conn: libvirt.virConnect,
        keep_count: Optional[int] = None,
        keep_days: Optional[int] = None,
        dry_run: bool = False,
    ) -> List[str]:
        """
        Clean up old backups according to a retention policy.
        Defaults to keeping the 3 most recent backups.
        Returns the list of cleaned up backup names.
        """
        if keep_count is None and keep_days is None:
            keep_count = 3

        cleaned_backups = []

        try:
            if backup_type == BackupType.SNAPSHOT:
                cleaned_backups = self._cleanup_snapshot_backups(
                    vm_name, conn, keep_count, keep_days, dry_run
                )
            elif backup_type == BackupType.OVERLAY:
                cleaned_backups = self._cleanup_overlay_backups(vm_name, conn, dry_run)
            else:
                raise ValueError(f"Unsupported backup type for cleanup: {backup_type}")

            logging.info(f"Cleaned up {len(cleaned_backups)} old backups for {vm_name}")

        except Exception as e:
            logging.error(f"Error during backup cleanup for {vm_name}: {e}")

        return cleaned_backups

    def _cleanup_snapshot_backups(
        self,
        vm_name: str,
        conn: libvirt.virConnect,
        keep_count: Optional[int] = None,
        keep_days: Optional[int] = None,
        dry_run: bool = False,
    ) -> List[str]:
        """Clean up old snapshot backups."""
        try:
            domain = conn.lookupByName(vm_name)
            snapshots = get_vm_snapshots(domain)

            # Filter automated backups (those with _backup_ in the name)
            auto_snapshots = [s for s in snapshots if "_backup_" in s["name"]]
            auto_snapshots.sort(key=lambda x: x["creation_time"], reverse=True)

            snapshots_to_delete = []

            if keep_count and len(auto_snapshots) > keep_count:
                snapshots_to_delete.extend(auto_snapshots[keep_count:])

            if keep_days:
                cutoff_date = datetime.datetime.now() - datetime.timedelta(days=keep_days)
                old_snapshots = [s for s in auto_snapshots if s["creation_time"] < cutoff_date]
                snapshots_to_delete.extend(old_snapshots)

            # Remove duplicates
            snapshots_to_delete = list({s["name"]: s for s in snapshots_to_delete}.values())

            deleted_names = []
            for snapshot in snapshots_to_delete:
                if dry_run:
                    deleted_names.append(snapshot["name"])
                    continue

                try:
                    delete_vm_snapshot(domain, snapshot["name"])
                    deleted_names.append(snapshot["name"])

                    # Remove metadata file if it exists
                    metadata_file = self.backup_metadata_dir / f"{snapshot['name']}.json"
                    if metadata_file.exists():
                        metadata_file.unlink()

                except Exception as e:
                    logging.warning(f"Failed to delete snapshot {snapshot['name']}: {e}")

            return deleted_names

        except Exception as e:
            logging.error(f"Error cleaning up snapshots for {vm_name}: {e}")
            return []

    def _cleanup_overlay_backups(
        self, vm_name: str, conn: libvirt.virConnect, dry_run: bool = False
    ) -> List[str]:
        """Clean up old overlay backups by deleting backup overlay volumes."""
        try:
            from .vm_queries import get_overlay_disks

            domain = conn.lookupByName(vm_name)
            overlay_paths = get_overlay_disks(domain)

            deleted_names = []
            for overlay_path in overlay_paths:
                if not overlay_path:
                    continue

                overlay_name = os.path.basename(overlay_path)
                if "_backup_" not in overlay_name and "_overlay_" not in overlay_name:
                    continue

                if not dry_run:
                    try:
                        vol, _ = _find_vol_by_path(conn, overlay_path)
                        if vol:
                            vol.delete(0)
                    except Exception as e:
                        logging.warning(f"Failed to delete overlay volume '{overlay_path}': {e}")

                deleted_names.append(overlay_name)

            return deleted_names

        except Exception as e:
            logging.error(f"Error cleaning up overlay backups for {vm_name}: {e}")
            return []

    def get_backup_status(self, backup_name: str) -> Optional[Dict[str, Any]]:
        """Get status and metadata for a specific backup."""
        return self._load_backup_metadata(backup_name)

    def list_backups(self, vm_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all backups with metadata."""
        backups = []

        for metadata_file in self.backup_metadata_dir.glob("*.json"):
            try:
                with open(metadata_file, "r") as f:
                    metadata = json.load(f)

                if vm_name is None or metadata.get("vm_name") == vm_name:
                    backups.append(metadata)

            except Exception as e:
                logging.warning(f"Error loading backup metadata from {metadata_file}: {e}")

        # Sort by creation time, newest first
        backups.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return backups

    def list_available_backups(self, vm_name: Optional[str] = None) -> List[str]:
        """List names of available backups for restoration."""
        backups = self.list_backups(vm_name)
        return [backup.get("name", "") for backup in backups if backup.get("name")]

    def restore_backup(
        self,
        domain: libvirt.virDomain,
        backup_name: str,
    ) -> Dict[str, Any]:
        """
        Restore a backup.

        Returns a dictionary with restoration results and metadata.
        """
        start_time = datetime.datetime.now()
        restore_metadata = {
            "backup_name": backup_name,
            "vm_name": domain.name(),
            "restore_started_at": start_time.isoformat(),
        }

        try:
            backup_metadata = self._load_backup_metadata(backup_name)
            if not backup_metadata:
                raise Exception(f"Backup metadata not found for '{backup_name}'")

            restore_metadata["backup_type"] = backup_metadata.get("type")
            restore_metadata["backup_created_at"] = backup_metadata.get("created_at")

            backup_type = backup_metadata["type"]
            if backup_type == BackupType.SNAPSHOT.value:
                result = self._restore_snapshot_backup(domain, backup_name, backup_metadata)
            elif backup_type == BackupType.OVERLAY.value:
                result = self._restore_overlay_backup(domain, backup_name, backup_metadata)
            else:
                raise ValueError(f"Unsupported backup type for restore: {backup_type}")

            restore_metadata.update(result)

            end_time = datetime.datetime.now()
            restore_metadata["restore_completed_at"] = end_time.isoformat()
            restore_metadata["restore_duration_seconds"] = (end_time - start_time).total_seconds()
            restore_metadata["success"] = True

            logging.info(f"Backup {backup_name} restored successfully")
            return restore_metadata

        except Exception as e:
            restore_metadata["error"] = str(e)
            restore_metadata["restore_completed_at"] = datetime.datetime.now().isoformat()
            restore_metadata["success"] = False

            logging.error(f"Backup {backup_name} restore failed: {e}")
            raise

    def _restore_snapshot_backup(
        self, domain: libvirt.virDomain, backup_name: str, backup_metadata: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Restore a VM from a snapshot backup."""
        from .vm_actions import restore_vm_snapshot

        snapshots = get_vm_snapshots(domain)
        snapshot_info = next((s for s in snapshots if s["name"] == backup_name), None)

        if not snapshot_info:
            raise Exception(f"Snapshot '{backup_name}' not found for restore")

        restore_vm_snapshot(domain, backup_name)

        return {
            "restore_method": "snapshot",
            "snapshot_name": backup_name,
            "snapshot_info": snapshot_info,
        }

    def _restore_overlay_backup(
        self, domain: libvirt.virDomain, backup_name: str, backup_metadata: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Restore a VM from an overlay backup by reverting to the backing file."""
        conn = domain.connect()
        restored_disks = []

        overlay_paths = backup_metadata.get("overlay_paths", [])
        processed_paths = backup_metadata.get("processed_paths", [])
        backing_paths = backup_metadata.get("backing_paths", {})

        if not overlay_paths and not processed_paths:
            raise Exception("No overlay backup files found in metadata")

        if processed_paths:
            # Compressed/encrypted backup - decompress and restore each overlay
            original_paths = backup_metadata.get("original_paths", overlay_paths)
            for i, processed_path in enumerate(processed_paths):
                overlay_path = original_paths[i] if i < len(original_paths) else None
                backing_path = backing_paths.get(overlay_path, "") if overlay_path else ""

                if not self._decompress_and_restore(processed_path, overlay_path, conn):
                    logging.warning(f"Failed to restore overlay {processed_path}")
                    continue

                restored_disks.append(
                    {
                        "overlay_path": overlay_path,
                        "backing_path": backing_path,
                        "status": "restored_from_compressed",
                    }
                )
        else:
            # Uncompressed backup - discard the overlay, reverting to the backing file
            for overlay_path in overlay_paths:
                backing_path = backing_paths.get(overlay_path, "")

                if not self._discard_overlay_volume(domain, overlay_path, conn):
                    logging.warning(f"Failed to discard overlay {overlay_path}")
                    continue

                restored_disks.append(
                    {
                        "overlay_path": overlay_path,
                        "backing_path": backing_path,
                        "status": "discarded",
                    }
                )

        if not restored_disks:
            raise Exception("Failed to restore any overlay disks")

        return {
            "restore_method": "overlay",
            "restored_disks": restored_disks,
            "note": "Overlay backup restored - overlay discarded, VM uses backing file",
        }

    def _decompress_and_restore(
        self, processed_path: str, overlay_path: Optional[str], conn: libvirt.virConnect
    ) -> bool:
        """Decompress a processed overlay file and upload it back to its volume."""
        if not os.path.exists(processed_path):
            logging.error(f"Processed file not found: {processed_path}")
            return False

        temp_file = None
        try:
            decompressed_path = processed_path

            if processed_path.endswith(".gz"):
                temp_file = processed_path + ".decompressed"
                with gzip.open(processed_path, "rb") as f_in:
                    with open(temp_file, "wb") as f_out:
                        shutil.copyfileobj(f_in, f_out)
                decompressed_path = temp_file

            if overlay_path:
                vol, _ = _find_vol_by_path(conn, overlay_path)
                if vol is not None:
                    file_size = os.path.getsize(decompressed_path)
                    stream = conn.newStream(0)
                    vol.upload(stream, 0, file_size)
                    with open(decompressed_path, "rb") as f:
                        while True:
                            data = f.read(1024 * 1024)
                            if not data:
                                break
                            stream.send(data)
                    stream.finish()

            return True

        except Exception as e:
            logging.error(f"Failed to decompress and restore {processed_path}: {e}")
            return False

        finally:
            if temp_file and os.path.exists(temp_file):
                os.remove(temp_file)

    def _discard_overlay_volume(
        self, domain: libvirt.virDomain, overlay_path: str, conn: libvirt.virConnect
    ) -> bool:
        """Discard an overlay volume and revert the VM disk to its backing file."""
        vol, pool = _find_vol_by_path(conn, overlay_path)
        if not vol:
            logging.error(f"Overlay volume not found: {overlay_path}")
            return False

        # Determine the backing file from the volume XML
        root = ET.fromstring(vol.XMLDesc(0))
        backing = root.find("backingStore")
        if backing is None:
            logging.warning(f"No backing store for {overlay_path}, deleting overlay anyway")
            self._delete_volume_quietly(vol, overlay_path)
            return True

        backing_path_elem = backing.find("path")
        if backing_path_elem is None or not backing_path_elem.text:
            logging.error(f"Could not determine backing file path for {overlay_path}")
            return False

        backing_path = backing_path_elem.text
        backing_vol, backing_pool = _find_vol_by_path(conn, backing_path)

        # Rewrite the VM disk to point at the backing file
        vm_root = ET.fromstring(domain.XMLDesc(0))
        updated = False
        for disk in vm_root.findall(".//disk"):
            source = disk.find("source")
            if source is None:
                continue

            path = source.get("file") or source.get("dev")
            is_volume_ref = (
                pool is not None
                and source.get("pool") == pool.name()
                and source.get("volume") == vol.name()
            )

            if path == overlay_path or is_volume_ref:
                if backing_pool and backing_vol:
                    source.set("pool", backing_pool.name())
                    source.set("volume", backing_vol.name())
                    source.attrib.pop("file", None)
                    source.attrib.pop("dev", None)
                else:
                    source.set("file", backing_path)
                    source.attrib.pop("pool", None)
                    source.attrib.pop("volume", None)
                updated = True
                break

        if updated:
            conn.defineXML(ET.tostring(vm_root, encoding="unicode"))

        self._delete_volume_quietly(vol, overlay_path)
        return True

    def _delete_volume_quietly(self, vol, overlay_path: str):
        """Delete a volume, logging (but not raising) on failure."""
        try:
            vol.delete(0)
        except Exception as e:
            logging.warning(f"Failed to delete overlay volume '{overlay_path}': {e}")
