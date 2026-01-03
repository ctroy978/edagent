"""File handling utilities for processing uploaded files."""

import os
import shutil
import tempfile
import zipfile
import json
from pathlib import Path
from typing import List, Tuple
from langchain_core.tools import tool

# Import conversion libraries
try:
    from PIL import Image
except ImportError:
    Image = None

try:
    from docx import Document
except ImportError:
    Document = None

try:
    from fpdf import FPDF
except ImportError:
    FPDF = None


@tool
def prepare_files_for_grading(file_paths: List[str]) -> str:
    """Process and convert uploaded files into a clean directory of PDFs for grading.

    Handles:
    - PDF: Copies directly
    - ZIP: Extracts and flattens
    - Images (JPG, PNG, etc.): Converts to PDF
    - Word (DOCX): Rejects with instruction (no longer supported)
    - Google Docs: Rejects with instruction

    Args:
        file_paths: List of paths to uploaded files (can be mixed types)

    Returns:
        JSON-formatted string containing:
        - directory_path: Path to temp dir with ready PDFs
        - file_count: Number of PDFs ready
        - warnings: List of issues/rejected files
    """
    try:
        # Create a temporary directory
        temp_dir = tempfile.mkdtemp(prefix="edagent_grading_")
        
        warnings = []
        processed_count = 0

        # Helper to process a single file (extracted or uploaded)
        def process_single_file(src_path, dest_dir):
            nonlocal processed_count
            filename = os.path.basename(src_path)
            name, ext = os.path.splitext(filename)
            ext = ext.lower()

            # Handle PDF
            if ext == '.pdf':
                shutil.copy2(src_path, os.path.join(dest_dir, filename))
                processed_count += 1
                return

            # Handle Google Docs shortcuts and Word documents (no longer supported)
            if ext in ['.gdoc', '.gsheet', '.gslides']:
                warnings.append(f"Rejected Google Doc shortcut: {filename}. Please export as PDF from Google Drive.")
                return

            if ext == '.docx':
                warnings.append(f"Rejected Word document: {filename}. Please export as PDF before uploading.")
                return

            # Handle Images
            if ext in ['.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif']:
                if Image:
                    try:
                        img_path = os.path.join(dest_dir, f"{name}.pdf")
                        image = Image.open(src_path)
                        if image.mode != 'RGB':
                            image = image.convert('RGB')
                        image.save(img_path)
                        processed_count += 1
                    except Exception as e:
                        warnings.append(f"Failed to convert image {filename}: {str(e)}")
                else:
                    warnings.append(f"Cannot convert {filename}: Pillow library not available.")
                return

            # Unknown type
            if not filename.startswith('.') and not filename.startswith('__MACOSX'):
                warnings.append(f"Skipped unsupported file type: {filename}")


        # Iterate through uploaded files
        for file_path in file_paths:
            if not os.path.exists(file_path):
                warnings.append(f"File not found: {file_path}")
                continue

            # Handle ZIPs
            if file_path.lower().endswith('.zip'):
                try:
                    with tempfile.TemporaryDirectory() as zip_temp:
                        with zipfile.ZipFile(file_path, "r") as zip_ref:
                            zip_ref.extractall(zip_temp)
                        
                        # Walk and process everything in ZIP
                        for root, dirs, files in os.walk(zip_temp):
                            for file in files:
                                if not file.startswith('.') and not file.startswith('__MACOSX'):
                                    src = os.path.join(root, file)
                                    process_single_file(src, temp_dir)
                except Exception as e:
                    warnings.append(f"Failed to process ZIP {os.path.basename(file_path)}: {str(e)}")
            
            # Handle Single Files
            else:
                process_single_file(file_path, temp_dir)

        result = {
            "directory_path": temp_dir,
            "file_count": processed_count,
            "warnings": warnings,
            "status": "success" if processed_count > 0 else "error"
        }
        
        return json.dumps(result, indent=2)

    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@tool
def extract_zip_to_temp(zip_path: str) -> str:
    """Extract a ZIP file to a temporary directory and report contents.

    Args:
        zip_path: Path to the ZIP file to extract

    Returns:
        Detailed report of extraction including directory path and file list
    """
    try:
        # Create a temporary directory
        temp_dir = tempfile.mkdtemp(prefix="edagent_essays_")

        # Extract the ZIP
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            zip_ref.extractall(temp_dir)

        # Flatten directory structure: Move all files to root temp_dir
        for root, dirs, files in os.walk(temp_dir):
            if root == temp_dir:
                continue  # Skip root directory

            for file in files:
                if not file.startswith('.') and not file.startswith('__MACOSX'):
                    src_path = os.path.join(root, file)
                    dst_path = os.path.join(temp_dir, file)

                    # Handle filename collisions
                    counter = 1
                    base, ext = os.path.splitext(file)
                    while os.path.exists(dst_path):
                        dst_path = os.path.join(temp_dir, f"{base}_{counter}{ext}")
                        counter += 1

                    shutil.move(src_path, dst_path)

        # List all extracted files
        extracted_files = []
        for root, dirs, files in os.walk(temp_dir):
            for file in files:
                # Skip hidden files and system files
                if not file.startswith('.') and not file.startswith('__MACOSX'):
                    file_path = os.path.join(root, file)
                    file_ext = os.path.splitext(file)[1].lower()
                    extracted_files.append(f"{file} ({file_ext})")

        # Categorize files
        pdfs = [f for f in extracted_files if '.pdf' in f.lower()]
        texts = [f for f in extracted_files if any(ext in f.lower() for ext in ['.txt', '.md', '.doc'])]
        other = [f for f in extracted_files if f not in pdfs and f not in texts]

        # Create detailed report
        report = f"✓ ZIP extracted successfully to: {temp_dir}\n\n"
        report += f"Found {len(extracted_files)} files:\n"

        if pdfs:
            report += f"\nPDF files ({len(pdfs)}):\n"
            for pdf in pdfs:
                report += f"  - {pdf}\n"

        if texts:
            report += f"\nText files ({len(texts)}):\n"
            for txt in texts:
                report += f"  - {txt}\n"

        if other:
            report += f"\nOther files ({len(other)}):\n"
            for oth in other:
                report += f"  - {oth}\n"

        if not extracted_files:
            report += "\n⚠️ Warning: No files found in ZIP (might be empty or corrupted)"

        return report

    except Exception as e:
        return f"Error extracting ZIP: {str(e)}"


@tool
def organize_pdfs_to_temp(pdf_paths: List[str]) -> str:
    """Copy multiple PDF files to a temporary directory for batch processing.

    Args:
        pdf_paths: List of paths to PDF files

    Returns:
        Path to the temporary directory containing organized PDFs
    """
    try:
        # Create a temporary directory
        temp_dir = tempfile.mkdtemp(prefix="edagent_essays_")

        # Copy PDFs to temp directory
        for pdf_path in pdf_paths:
            if os.path.exists(pdf_path) and pdf_path.lower().endswith(".pdf"):
                dest = os.path.join(temp_dir, os.path.basename(pdf_path))
                shutil.copy2(pdf_path, dest)

        return temp_dir
    except Exception as e:
        return f"Error organizing PDFs: {str(e)}"


@tool
def read_text_file(file_path: str) -> str:
    """Read contents of a text file (rubrics, prompts, etc.).

    Args:
        file_path: Path to the text file

    Returns:
        Contents of the file as a string
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"Error reading file: {str(e)}"


@tool
def list_directory_files(directory_path: str, extension: str = ".pdf") -> str:
    """List files in a directory with a specific extension.

    Args:
        directory_path: Path to the directory
        extension: File extension to filter (default: .pdf)

    Returns:
        Formatted list of files found
    """
    try:
        path = Path(directory_path)
        if not path.exists():
            return f"Directory not found: {directory_path}"

        files = list(path.glob(f"*{extension}"))
        if not files:
            return f"No {extension} files found in {directory_path}"

        file_list = "\n".join([f"- {f.name}" for f in files])
        return f"Found {len(files)} {extension} files:\n{file_list}"
    except Exception as e:
        return f"Error listing directory: {str(e)}"


def parse_attached_files(message_content: str) -> Tuple[List[str], str]:
    """Parse attached file paths from Chainlit message content.

    Args:
        message_content: The message content that may contain file attachments

    Returns:
        Tuple of (list of file paths, cleaned message without file annotations)
    """
    # Look for "[User attached files: ...]" pattern
    if "[User attached files:" not in message_content:
        return [], message_content

    # Extract the file paths section
    start = message_content.find("[User attached files:")
    end = message_content.find("]", start)

    if start == -1 or end == -1:
        return [], message_content

    files_section = message_content[start : end + 1]
    files_str = (
        files_section.replace("[User attached files:", "").replace("]", "").strip()
    )

    # Split by comma and clean up paths
    file_paths = [p.strip() for p in files_str.split(",")]

    # Remove the files annotation from message
    cleaned_message = message_content[:start] + message_content[end + 1 :]
    cleaned_message = cleaned_message.strip()

    return file_paths, cleaned_message


def categorize_uploaded_files(file_paths: List[str]) -> dict:
    """Categorize uploaded files by type.

    Args:
        file_paths: List of file paths

    Returns:
        Dictionary with categorized files: {pdfs: [...], zips: [...], texts: [...], other: [...]}
    """
    categorized = {"pdfs": [], "zips": [], "texts": [], "other": []}

    for path in file_paths:
        path_lower = path.lower()
        if path_lower.endswith(".pdf"):
            categorized["pdfs"].append(path)
        elif path_lower.endswith(".zip"):
            categorized["zips"].append(path)
        elif path_lower.endswith((".txt", ".md", ".rtf")):
            categorized["texts"].append(path)
        else:
            categorized["other"].append(path)

    return categorized