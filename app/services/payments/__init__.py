"""Payments services package for 3X-UI ShopBot.
Provides integration modules for AnyPay and Platega.io payment gateways.
"""
from .anypay import AnyPayService
from .platega import PlategaService

__all__ = [
    "AnyPayService",
    "PlategaService",
]

