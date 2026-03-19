"""
Plugin entry point for mental_health plugin.

This module provides the required get_plugin() function for auto-discovery.
"""

from .mental_health_plugin import MentalHealthPlugin


def get_plugin():
    """
    Required function that returns plugin instance.

    Called by the plugin discovery system during application startup.

    Returns:
        MentalHealthPlugin: Instance of the mental health SOAP notes plugin
    """
    return MentalHealthPlugin()
