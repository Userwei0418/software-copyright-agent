#!/usr/bin/env python3
"""Build legible page contact sheets for document visual QA."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def _natural_key(path: Path) -> tuple[object, ...]:
    parts: list[object] = []
    current = ""
    numeric = False
    for character in path.stem:
        if character.isdigit() != numeric and current:
            parts.append(int(current) if numeric else current.lower())
            current = ""
        current += character
        numeric = character.isdigit()
    if current:
        parts.append(int(current) if numeric else current.lower())
    return tuple(parts)


def build_contact_sheets(
    input_dir: Path,
    output_dir: Path,
    *,
    columns: int = 4,
    pages_per_sheet: int = 12,
    thumbnail_width: int = 420,
) -> list[Path]:
    pages = sorted(input_dir.glob("page-*.png"), key=_natural_key)
    if not pages:
        raise RuntimeError(f"No page PNG files found in {input_dir}")
    if columns < 1 or pages_per_sheet < 1 or thumbnail_width < 120:
        raise ValueError("Invalid contact sheet geometry")

    output_dir.mkdir(parents=True, exist_ok=True)
    label_height = 32
    gutter = 20
    outputs: list[Path] = []
    for sheet_index, offset in enumerate(range(0, len(pages), pages_per_sheet), 1):
        batch = pages[offset:offset + pages_per_sheet]
        thumbnails: list[tuple[Path, Image.Image]] = []
        max_height = 0
        for page in batch:
            with Image.open(page) as source:
                ratio = thumbnail_width / source.width
                thumb = source.convert("RGB").resize(
                    (thumbnail_width, round(source.height * ratio)),
                    Image.Resampling.LANCZOS,
                )
            thumbnails.append((page, thumb))
            max_height = max(max_height, thumb.height)

        rows = math.ceil(len(batch) / columns)
        canvas = Image.new(
            "RGB",
            (
                gutter + columns * (thumbnail_width + gutter),
                gutter + rows * (max_height + label_height + gutter),
            ),
            "#d9dee5",
        )
        draw = ImageDraw.Draw(canvas)
        font = ImageFont.load_default(size=20)
        for index, (page, thumb) in enumerate(thumbnails):
            row, column = divmod(index, columns)
            x = gutter + column * (thumbnail_width + gutter)
            y = gutter + row * (max_height + label_height + gutter)
            canvas.paste(thumb, (x, y))
            draw.text((x, y + max_height + 4), page.stem, fill="#1f2937", font=font)

        destination = output_dir / f"contact-sheet-{sheet_index:02d}.png"
        canvas.save(destination, optimize=True)
        outputs.append(destination)
    return outputs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--columns", type=int, default=4)
    parser.add_argument("--pages-per-sheet", type=int, default=12)
    parser.add_argument("--thumbnail-width", type=int, default=420)
    args = parser.parse_args()
    outputs = build_contact_sheets(
        args.input_dir,
        args.output_dir,
        columns=args.columns,
        pages_per_sheet=args.pages_per_sheet,
        thumbnail_width=args.thumbnail_width,
    )
    for output in outputs:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
