"""Helpers the functions share: a module in functions/ is imported by name."""
import re


def record_key(file_name):
    """clips/My page.txt -> clip:My_page.txt (record keys allow fewer characters than file names)."""
    return "clip:" + re.sub(r"[^A-Za-z0-9_.:-]", "_", file_name.removeprefix("clips/"))[:120]


def describe(file_name, text):
    words = text.split()
    return {"file": file_name, "words": len(words), "preview": " ".join(words)[:200]}
