"""Fetch the pinned official NVIDIA model for an optional Docker VAD build."""
import argparse
import hashlib
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

URL = "https://api.ngc.nvidia.com/v2/models/nvidia/nemo/vad_multilingual_marblenet/versions/1.10.0/files/vad_multilingual_marblenet.nemo"
SHA256 = "71de4645391bc800c5e9e91b1a828a3496447a079d8052bdc4db9d52f17f673c"


def download(destination):
    url = URL
    for _ in range(4):
        if urlparse(url).scheme != "https":
            raise ValueError("Model download requires HTTPS")
        with urlopen(url, timeout=120) as response:
            location = response.headers.get("Location")
            if location:
                url = location
                continue
            data = response.read(2 * 1024 * 1024)
        if hashlib.sha256(data).hexdigest() != SHA256:
            raise ValueError("NVIDIA model checksum mismatch; refusing to install")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".tmp")
        temporary.write_bytes(data)
        temporary.replace(destination)
        return
    raise RuntimeError("NVIDIA model download exceeded redirect limit")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    download(parser.parse_args().destination)
