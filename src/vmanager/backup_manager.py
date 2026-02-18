"""
Backup Manager - Core backup operations for VirtUI Manager

This module provides backup functionality including:
- Multiple backup type support (snapshots, overlays)
- Compression and encryption
- Backup verification and testing
- Backup restoration
"""

import gzip
import hashlib
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
import datetime
import json

import libvirt

# Backup classes definitions
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
    verify: bool = False
    quiesce: bool = False


@dataclass
class RetentionPolicy:
    """Retention policy for backup cleanup."""

    keep_count: Optional[int] = None
    keep_days: Optional[int] = None


from .storage_manager import create_overlay_volume, delete_volume
from .vm_actions import create_vm_snapshot, delete_vm_snapshot
from .vm_queries import get_vm_snapshots
from .libvirt_utils import _find_vol_by_path


class BackupVerificationError(Exception):
    """Raised when backup verification fails."""

    pass


class BackupManager:
    """Advanced backup operations and management."""

    def __init__(self, config_dir: Optional[Path] = None):
        self.config_dir = config_dir or Path.home() / ".config" / "virtui-manager"
        self.backup_metadata_dir = self.config_dir / "backup_metadata"
        self.backup_metadata_dir.mkdir(parents=True, exist_ok=True)

        # Encryption settings (basic implementation)
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
        Create a backup with advanced options.

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
                "verify": options.verify,
                "quiesce": options.quiesce,
            },
        }

        try:
            if backup_type == BackupType.SNAPSHOT:
                result = self._create_snapshot_backup(domain, backup_name, options)
            elif backup_type == BackupType.OVERLAY:
                result = self._create_overlay_backup(domain, backup_name, options)
            else:
                raise ValueError(f"Unsupported backup type: {backup_type}")

            backup_metadata.update(result)

            # Calculate backup duration
            end_time = datetime.datetime.now()
            backup_metadata["completed_at"] = end_time.isoformat()
            backup_metadata["duration_seconds"] = (end_time - start_time).total_seconds()

            # Verify backup if requested
            if options.verify:
                verification_result = self._verify_backup(domain, backup_name, backup_type, options)
                backup_metadata["verification"] = verification_result

            # Save metadata
            self._save_backup_metadata(backup_name, backup_metadata)

            logging.info(f"Backup {backup_name} created successfully")
            return backup_metadata

        except Exception as e:
            backup_metadata["error"] = str(e)
            backup_metadata["completed_at"] = datetime.datetime.now().isoformat()

            # Save error metadata
            self._save_backup_metadata(backup_name, backup_metadata)

            logging.error(f"Backup {backup_name} failed: {e}")
            raise

    def _create_snapshot_backup(
        self, domain: libvirt.virDomain, backup_name: str, options: BackupOptions
    ) -> Dict[str, Any]:
        """Create a VM snapshot backup."""
        description = f"Automated backup created at {datetime.datetime.now().isoformat()}"

        # Create the snapshot
        create_vm_snapshot(domain, backup_name, description, options.quiesce)

        # Get snapshot information
        snapshots = get_vm_snapshots(domain)
        snapshot_info = next((s for s in snapshots if s["name"] == backup_name), None)

        if not snapshot_info:
            raise Exception(f"Created snapshot {backup_name} not found")

        result = {
            "snapshot_info": snapshot_info,
            "size_bytes": 0,  # Snapshots don't have a direct size
            "checksum": None,
            "compressed": False,
            "encrypted": False,
        }

        return result

    def _create_overlay_backup(
        self, domain: libvirt.virDomain, backup_name: str, options: BackupOptions
    ) -> Dict[str, Any]:
        """Create an overlay-based backup."""
        conn = domain.connect()

        # Get VM disk information
        from .vm_queries import get_vm_disks_info, _get_domain_root

        domain_xml = domain.XMLDesc(0)
        root = _get_domain_root(domain_xml)
        disks = get_vm_disks_info(conn, root)

        if not disks:
            raise Exception("No disks found for overlay backup")

        overlay_paths = []
        total_size = 0

        try:
            for disk in disks:
                if disk.get("device") != "disk":
                    continue

                disk_path = disk.get("path")
                if not disk_path:
                    continue

                # Find the storage pool for this disk
                vol, pool = _find_vol_by_path(conn, disk_path)
                if not pool:
                    logging.warning(f"Could not find pool for disk {disk_path}")
                    continue

                # Create overlay volume
                overlay_name = f"{backup_name}_{os.path.basename(disk_path)}_overlay.qcow2"
                overlay_vol = create_overlay_volume(pool, overlay_name, disk_path)
                overlay_path = overlay_vol.path()
                overlay_paths.append(overlay_path)

                # Get size information
                _, capacity, allocation = overlay_vol.info()
                total_size += allocation

        except Exception as e:
            # Cleanup any created overlays on failure
            for overlay_path in overlay_paths:
                try:
                    vol, _ = _find_vol_by_path(conn, overlay_path)
                    if vol:
                        delete_volume(vol)
                except:
                    pass
            raise e

        result = {
            "overlay_paths": overlay_paths,
            "size_bytes": total_size,
            "checksum": None,
            "compressed": options.compress,
            "encrypted": options.encrypt,
        }

        # Apply compression and encryption if requested
        if options.compress or options.encrypt:
            result = self._process_overlay_files(overlay_paths, backup_name, options)

        return result

    def _process_overlay_files(
        self, overlay_paths: List[str], backup_name: str, options: BackupOptions
    ) -> Dict[str, Any]:
        """Process overlay files with compression and encryption."""
        processed_paths = []
        total_size = 0
        checksums = []

        for overlay_path in overlay_paths:
            processed_path, size, checksum = self._process_file(overlay_path, backup_name, options)
            processed_paths.append(processed_path)
            total_size += size
            checksums.append(checksum)

        return {
            "processed_paths": processed_paths,
            "original_paths": overlay_paths,
            "size_bytes": total_size,
            "checksums": checksums,
            "compressed": options.compress,
            "encrypted": options.encrypt,
        }

    def _process_file(
        self, file_path: str, backup_name: str, options: BackupOptions
    ) -> Tuple[str, int, str]:
        """
        Process a file with compression and/or encryption.
        Returns: (processed_path, size, checksum)
        """
        processed_path = file_path

        # Compression
        if options.compress:
            compressed_path = f"{file_path}.gz"
            with open(file_path, "rb") as f_in:
                with gzip.open(compressed_path, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
            processed_path = compressed_path

        # Encryption (basic implementation)
        if options.encrypt:
            encrypted_path = f"{processed_path}.enc"
            self._encrypt_file(processed_path, encrypted_path)
            if processed_path != file_path:  # Remove intermediate compressed file
                os.remove(processed_path)
            processed_path = encrypted_path

        # Calculate size and checksum of final processed file
        size = os.path.getsize(processed_path)
        checksum = self._calculate_checksum(processed_path)

        return processed_path, size, checksum

    def _encrypt_file(self, input_path: str, output_path: str):
        """Basic file encryption using OpenSSL."""
        # This is a basic implementation - in production, use proper encryption libraries
        key = self._get_encryption_key()

        try:
            # Use openssl for basic encryption
            subprocess.run(
                [
                    "openssl",
                    "aes-256-cbc",
                    "-salt",
                    "-in",
                    input_path,
                    "-out",
                    output_path,
                    "-k",
                    key,
                ],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as e:
            raise Exception(f"Encryption failed: {e.stderr.decode()}")
        except FileNotFoundError:
            raise Exception("OpenSSL not found - encryption requires openssl to be installed")

    def _get_encryption_key(self) -> str:
        """Get or generate encryption key."""
        if self.encryption_key_file.exists():
            with open(self.encryption_key_file, "r") as f:
                return f.read().strip()
        else:
            # Generate a new key
            import secrets

            key = secrets.token_hex(32)
            with open(self.encryption_key_file, "w") as f:
                f.write(key)
            # Secure file permissions
            os.chmod(self.encryption_key_file, 0o600)
            return key

    def _calculate_checksum(self, file_path: str) -> str:
        """Calculate SHA256 checksum of a file."""
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                sha256_hash.update(chunk)
        return sha256_hash.hexdigest()

    def _verify_backup(
        self,
        domain: libvirt.virDomain,
        backup_name: str,
        backup_type: BackupType,
        options: BackupOptions,
    ) -> Dict[str, Any]:
        """Verify backup integrity."""
        verification_result = {
            "verified_at": datetime.datetime.now().isoformat(),
            "success": False,
            "checks": [],
        }

        try:
            if backup_type == BackupType.SNAPSHOT:
                result = self._verify_snapshot_backup(domain, backup_name)
            elif backup_type == BackupType.OVERLAY:
                result = self._verify_overlay_backup(backup_name)
            else:
                raise ValueError(f"Unsupported backup type for verification: {backup_type}")

            verification_result.update(result)
            verification_result["success"] = True

        except Exception as e:
            verification_result["error"] = str(e)
            logging.error(f"Backup verification failed for {backup_name}: {e}")

        return verification_result

    def _verify_snapshot_backup(
        self, domain: libvirt.virDomain, backup_name: str
    ) -> Dict[str, Any]:
        """Verify snapshot backup integrity."""
        checks = []

        # Check if snapshot exists
        snapshots = get_vm_snapshots(domain)
        snapshot = next((s for s in snapshots if s["name"] == backup_name), None)

        if not snapshot:
            raise BackupVerificationError(f"Snapshot {backup_name} not found")

        checks.append(
            {"type": "existence", "description": "Snapshot exists in libvirt", "success": True}
        )

        # Check snapshot state
        if snapshot.get("state"):
            checks.append(
                {
                    "type": "state",
                    "description": f"Snapshot state: {snapshot['state']}",
                    "success": True,
                }
            )

        return {"checks": checks}

    def _verify_overlay_backup(self, backup_name: str) -> Dict[str, Any]:
        """Verify overlay backup integrity."""
        # Load backup metadata
        metadata = self._load_backup_metadata(backup_name)
        if not metadata:
            raise BackupVerificationError(f"No metadata found for backup {backup_name}")

        checks = []

        # Verify files exist and checksums match
        overlay_paths = metadata.get("overlay_paths", [])
        processed_paths = metadata.get("processed_paths", overlay_paths)
        expected_checksums = metadata.get("checksums", [])

        for i, path in enumerate(processed_paths):
            if not os.path.exists(path):
                checks.append(
                    {
                        "type": "file_existence",
                        "description": f"File {path}",
                        "success": False,
                        "error": "File not found",
                    }
                )
                continue

            checks.append(
                {"type": "file_existence", "description": f"File {path}", "success": True}
            )

            # Verify checksum if available
            if i < len(expected_checksums):
                actual_checksum = self._calculate_checksum(path)
                expected_checksum = expected_checksums[i]

                checks.append(
                    {
                        "type": "checksum",
                        "description": f"Checksum for {os.path.basename(path)}",
                        "success": actual_checksum == expected_checksum,
                        "expected": expected_checksum,
                        "actual": actual_checksum,
                    }
                )

        return {"checks": checks}

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
        server_name: str,
        retention: RetentionPolicy,
        backup_type: BackupType,
        conn: libvirt.virConnect,
    ) -> List[str]:
        """
        Clean up old backups according to retention policy.
        Returns list of cleaned up backup names.
        """
        cleaned_backups = []

        try:
            if backup_type == BackupType.SNAPSHOT:
                cleaned_backups = self._cleanup_snapshot_backups(vm_name, retention, conn)
            elif backup_type == BackupType.OVERLAY:
                cleaned_backups = self._cleanup_overlay_backups(vm_name, retention)
            else:
                raise ValueError(f"Unsupported backup type for cleanup: {backup_type}")

            logging.info(f"Cleaned up {len(cleaned_backups)} old backups for {vm_name}")

        except Exception as e:
            logging.error(f"Error during backup cleanup for {vm_name}: {e}")

        return cleaned_backups

    def _cleanup_snapshot_backups(
        self, vm_name: str, retention: RetentionPolicy, conn: libvirt.virConnect
    ) -> List[str]:
        """Clean up old snapshot backups."""
        try:
            domain = conn.lookupByName(vm_name)
            snapshots = get_vm_snapshots(domain)

            # Filter automated backups (those with _backup_ in the name)
            auto_snapshots = [s for s in snapshots if "_backup_" in s["name"]]
            auto_snapshots.sort(key=lambda x: x["creation_time"], reverse=True)

            snapshots_to_delete = []

            # Apply retention policies
            if retention.keep_count and len(auto_snapshots) > retention.keep_count:
                snapshots_to_delete.extend(auto_snapshots[retention.keep_count :])

            if retention.keep_days:
                cutoff_date = datetime.datetime.now() - datetime.timedelta(days=retention.keep_days)
                old_snapshots = [s for s in auto_snapshots if s["creation_time"] < cutoff_date]
                snapshots_to_delete.extend(old_snapshots)

            # Remove duplicates
            snapshots_to_delete = list({s["name"]: s for s in snapshots_to_delete}.values())

            # Delete the snapshots
            deleted_names = []
            for snapshot in snapshots_to_delete:
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

    def _cleanup_overlay_backups(self, vm_name: str, retention: RetentionPolicy) -> List[str]:
        """Clean up old overlay backups."""
        # TODO: Implement overlay backup cleanup
        # This would involve tracking overlay files and applying retention policies
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

    def restore_backup(
        self,
        domain: libvirt.virDomain,
        backup_name: str,
        verify_before_restore: bool = True,
    ) -> Dict[str, Any]:
        """
        Restore a backup.

        Args:
            domain: The libvirt domain to restore to
            backup_name: Name of the backup to restore
            verify_before_restore: Whether to verify backup integrity before restoring

        Returns:
            Dictionary with restoration results and metadata
        """
        start_time = datetime.datetime.now()
        restore_metadata = {
            "backup_name": backup_name,
            "vm_name": domain.name(),
            "restore_started_at": start_time.isoformat(),
            "verify_before_restore": verify_before_restore,
        }

        try:
            # Load backup metadata
            backup_metadata = self._load_backup_metadata(backup_name)
            if not backup_metadata:
                raise Exception(f"Backup metadata not found for '{backup_name}'")

            restore_metadata["backup_type"] = backup_metadata.get("type")
            restore_metadata["backup_created_at"] = backup_metadata.get("created_at")

            # Verify backup before restore if requested
            if verify_before_restore:
                backup_type = BackupType(backup_metadata["type"])
                verification_result = self._verify_backup(
                    domain, backup_name, backup_type, BackupOptions()
                )
                restore_metadata["pre_restore_verification"] = verification_result

                if not verification_result.get("success", False):
                    raise Exception("Backup verification failed before restore")

            # Perform restore based on backup type
            backup_type = backup_metadata["type"]
            if backup_type == BackupType.SNAPSHOT.value:
                result = self._restore_snapshot_backup(domain, backup_name, backup_metadata)
            elif backup_type == BackupType.OVERLAY.value:
                result = self._restore_overlay_backup(domain, backup_name, backup_metadata)
            else:
                raise ValueError(f"Unsupported backup type for restore: {backup_type}")

            restore_metadata.update(result)

            # Calculate restore duration
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

        # The backup name is the same as the snapshot name
        snapshot_name = backup_name

        # Check if snapshot exists
        snapshots = get_vm_snapshots(domain)
        snapshot_info = next((s for s in snapshots if s["name"] == snapshot_name), None)

        if not snapshot_info:
            raise Exception(f"Snapshot '{snapshot_name}' not found for restore")

        # Restore the snapshot
        restore_vm_snapshot(domain, snapshot_name)

        return {
            "restore_method": "snapshot",
            "snapshot_name": snapshot_name,
            "snapshot_info": snapshot_info,
        }

    def _restore_overlay_backup(
        self, domain: libvirt.virDomain, backup_name: str, backup_metadata: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Restore a VM from an overlay backup."""
        # This is more complex than snapshot restore
        # For overlay backups, we would need to:
        # 1. Decrypt and decompress the overlay files if needed
        # 2. Apply the overlay data back to the original disk images
        # 3. Update the VM configuration

        # For now, we'll implement a basic version
        overlay_paths = backup_metadata.get("overlay_paths", [])
        processed_paths = backup_metadata.get("processed_paths", overlay_paths)

        if not overlay_paths and not processed_paths:
            raise Exception("No overlay files found in backup metadata")

        # Check if overlay files still exist
        existing_files = []
        for path in processed_paths if processed_paths else overlay_paths:
            if os.path.exists(path):
                existing_files.append(path)

        if not existing_files:
            raise Exception("No overlay backup files found on disk")

        # For overlay restore, we would typically need to:
        # - Stop the VM if running
        # - Copy/merge the overlay data back to the original disks
        # - Handle decryption/decompression if needed
        # - Start the VM

        # This is a placeholder implementation
        return {
            "restore_method": "overlay",
            "overlay_files": existing_files,
            "note": "Overlay restore is not fully implemented yet - this is a placeholder",
        }

    def list_available_backups(self, vm_name: Optional[str] = None) -> List[str]:
        """List names of available backups for restoration."""
        backups = self.list_backups(vm_name)
        return [backup.get("name", "") for backup in backups if backup.get("name")]
