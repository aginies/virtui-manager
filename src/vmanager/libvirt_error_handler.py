"""
Libvirt error handling module.

This module provides custom error handling for libvirt operations, including
a custom error handler that properly logs libvirt errors using the Python
logging framework and functions to register the error handler with libvirt.
"""

import logging

import libvirt


def libvirt_error_handler(conn, error):  # pylint: disable=unused-argument
    """
    Custom libvirt error handler that logs errors to the logging framework.

    Args:
        conn: The libvirt connection object
        error: The error tuple from libvirt (code, domain, message, level, conn)
    """
    try:
        # Extract error components with safety checks
        code = error[0] if len(error) > 0 else 0
        domain = error[1] if len(error) > 1 else 0
        message = error[2] if len(error) > 2 else "Unknown error message"
        level = error[3] if len(error) > 3 else libvirt.VIR_ERR_ERROR
        conn_str = error[4] if len(error) > 4 else "Unknown connection"

        # Determine logging level - only use levels that exist
        if level == libvirt.VIR_ERR_ERROR:
            log_level = logging.ERROR
        elif level == libvirt.VIR_ERR_WARNING:
            log_level = logging.WARNING
        else:
            # Default to INFO for unknown levels
            log_level = logging.INFO

        # Log the error with more context
        logging.log(
            log_level,
            "libvirt error: code=%d, domain=%d, message='%s', level=%d, conn='%s'",
            code,
            domain,
            message,
            level,
            conn_str,
        )
    except Exception as e:  # pylint: disable=broad-exception-caught
        # Fallback logging if the error handler itself fails
        logging.error("Error in libvirt error handler: %s", e)


def register_error_handler():
    """
    Registers the custom libvirt error handler.
    """
    try:
        libvirt.registerErrorHandler(f=libvirt_error_handler, ctx=None)
        logging.info("Successfully registered custom libvirt error handler")
    except Exception as e:  # pylint: disable=broad-exception-caught
        logging.error("Failed to register libvirt error handler: %s", e)
