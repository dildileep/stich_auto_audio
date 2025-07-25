import pytest
from app import clean, get_hash
from io import BytesIO

def test_clean():
    assert clean("Hello, WORLD!!!") == "hello world"
    assert clean("a b  c") == "a b c"

def test_hash():
    data = b"123"
    assert get_hash(data) == "202cb962ac59075b964b07152d234b70"
