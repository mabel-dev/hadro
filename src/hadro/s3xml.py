"""Helpers for building and parsing S3 XML documents."""

from __future__ import annotations

from datetime import UTC, datetime
from xml.etree import ElementTree as ET

S3_NS = "http://s3.amazonaws.com/doc/2006-03-01/"


def element(tag: str, text: object | None = None, parent: ET.Element | None = None):
    node = ET.Element(tag) if parent is None else ET.SubElement(parent, tag)
    if text is not None:
        node.text = str(text)
    return node


def to_xml(root: ET.Element, namespace: bool = True) -> bytes:
    if namespace:
        root.set("xmlns", S3_NS)
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="utf-8")


def iso8601(moment: datetime) -> str:
    """Timestamp format used inside S3 XML bodies, e.g. 2009-10-12T17:50:30.000Z."""
    moment = moment.astimezone(UTC)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def find_text(node: ET.Element | None, tag: str, strip: bool = True) -> str | None:
    """Find a child's text, with or without the S3 namespace."""
    if node is None:
        return None
    child = node.find(f"{{{S3_NS}}}{tag}")
    if child is None:
        child = node.find(tag)
    if child is None or child.text is None:
        return None
    return child.text.strip() if strip else child.text


def find(node: ET.Element | None, tag: str) -> ET.Element | None:
    if node is None:
        return None
    child = node.find(f"{{{S3_NS}}}{tag}")
    return child if child is not None else node.find(tag)
