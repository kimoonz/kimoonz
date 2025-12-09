import tempfile
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter

from pdf_merger import merge_pdfs, validate_inputs


def create_sample_pdf(path: Path, pages: int = 1) -> None:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    with path.open("wb") as pdf_file:
        writer.write(pdf_file)


def test_validate_inputs_requires_two_files(tmp_path: Path):
    pdf_path = tmp_path / "single.pdf"
    create_sample_pdf(pdf_path)

    with pytest.raises(ValueError):
        validate_inputs([str(pdf_path)])


def test_merge_pdfs_combines_pages_in_order(tmp_path: Path):
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"
    create_sample_pdf(first, pages=2)
    create_sample_pdf(second, pages=3)

    output = tmp_path / "merged.pdf"
    merged_path = merge_pdfs([first, second], output)

    reader = PdfReader(str(merged_path))
    assert len(reader.pages) == 5


def test_validate_inputs_rejects_missing_files():
    with pytest.raises(FileNotFoundError):
        validate_inputs(["missing1.pdf", "missing2.pdf"])


def test_merge_pdfs_rejects_encrypted_input(tmp_path: Path):
    encrypted = tmp_path / "secret.pdf"
    create_sample_pdf(encrypted)

    # Encrypt the PDF after creation for the test.
    writer = PdfWriter()
    reader = PdfReader(str(encrypted))
    for page in reader.pages:
        writer.add_page(page)
    writer.encrypt(user_password="1234")
    with encrypted.open("wb") as encrypted_file:
        writer.write(encrypted_file)

    with pytest.raises(ValueError):
        merge_pdfs([encrypted, encrypted], tmp_path / "out.pdf")
