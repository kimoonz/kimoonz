"""Command-line utility to merge multiple PDF files into a single document.

Usage example:
    python pdf_merger.py output.pdf input1.pdf input2.pdf [input3.pdf ...]

The merge order follows the order of the input arguments.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable, List

from pypdf import PdfReader, PdfWriter


def validate_inputs(input_paths: Iterable[str]) -> List[Path]:
    """Convert input strings to Paths and ensure each file exists.

    Args:
        input_paths: Iterable of path strings provided by the user.

    Returns:
        A list of resolved ``Path`` objects.

    Raises:
        FileNotFoundError: If any input file does not exist.
        ValueError: If fewer than two input files are provided.
    """

    paths = [Path(path).expanduser().resolve() for path in input_paths]
    if len(paths) < 2:
        raise ValueError("At least two input PDF files are required to perform a merge.")

    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Input files not found: {', '.join(missing)}")

    return paths


def merge_pdfs(input_files: Iterable[Path], output_path: Path) -> Path:
    """Merge PDF files in the given order into a single output file.

    Args:
        input_files: Ordered list of PDF file paths to merge.
        output_path: Destination path for the merged PDF.

    Returns:
        The resolved output path.

    Raises:
        ValueError: If any PDF is encrypted and cannot be read.
    """

    writer = PdfWriter()

    for pdf_path in input_files:
        reader = PdfReader(str(pdf_path))
        if reader.is_encrypted:
            raise ValueError(f"Encrypted PDF encountered and cannot be merged: {pdf_path}")

        for page in reader.pages:
            writer.add_page(page)

    output_path = output_path.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("wb") as merged_file:
        writer.write(merged_file)

    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Merge multiple PDF files into a single PDF while preserving order."
    )
    parser.add_argument(
        "output",
        type=Path,
        help="Destination path for the merged PDF (e.g., merged.pdf)",
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        help="List of input PDF files in the desired merge order",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        input_paths = validate_inputs(args.inputs)
        output_path = Path(args.output)
        merged_path = merge_pdfs(input_paths, output_path)
    except (FileNotFoundError, ValueError) as error:
        print(f"Error: {error}")
        raise SystemExit(1) from error

    print(f"Merged {len(args.inputs)} files into {merged_path}")


if __name__ == "__main__":
    main()
