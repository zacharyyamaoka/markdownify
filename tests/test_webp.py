"""WebP is a citable image source. WHY: supplier material arrives as WebP (JMC's servo selection list, 2026-10-29),
and converting it to PNG first would cite a file the supplier never sent."""

import io

from PIL import Image

from markdownify import bundle, cache, fetch, image


def webp_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buffer, format="WEBP")
    return buffer.getvalue()


def test_webp_is_sniffed_from_its_bytes_not_its_name():
    assert fetch.media_type_of("application/octet-stream", webp_bytes()) == "image/webp"


def test_webp_is_citable_cached_and_converted_by_the_image_path():
    assert "image/webp" in fetch.CITABLE_MEDIA_TYPES
    assert cache.extension_of("image/webp") == "webp"
    assert "image/webp" in image.MEDIA_TYPES
    assert bundle.converter_for("image/webp").__name__ == "markdownify.image"
