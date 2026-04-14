import os
import re
import json
import time
from pathlib import Path
from collections import Counter
from xml.sax.saxutils import escape

import pdfplumber
import pytesseract
from PIL import Image
from flask import Flask, render_template, request, Response, jsonify
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_FOLDER = BASE_DIR / "uploads"
UPLOAD_FOLDER.mkdir(exist_ok=True)

ALLOWED_EXTENSIONS = {"pdf"}

app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = str(UPLOAD_FOLDER)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB

# Ak používaš Windows a Tesseract sa nenájde, odkomentuj:
# pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def parse_bool_form(value) -> bool:
    return value in ("on", "true", "True", "1")


def normalize_bullets(text: str) -> str:
    lines = text.split("\n")
    result = []
    i = 0

    while i < len(lines):
        line = lines[i].strip()

        if line == "-" and i + 1 < len(lines):
            next_line = lines[i + 1].strip()
            if next_line:
                result.append(f"- {next_line}")
                i += 2
                continue

        result.append(lines[i])
        i += 1

    return "\n".join(result)


def clean_text(text: str) -> str:
    if not text:
        return ""

    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")

    replacements = {
        "": "-",
        "•": "-",
        "▪": "-",
        "■": "-",
        "◦": "-",
        "○": "-",
        "♦": "-",
        "◆": "-",
        "‣": "-",
        "▶": "-",
        "►": "-",
        "∙": "-",
        "·": "-",
        "�": "",
        "▯": "",
        "□": "",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    text = re.sub(r"(?m)^\s*[^\w\s]\s*$", "-", text)

    lines = [re.sub(r"\s{2,}", " ", line.strip()) for line in text.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)

    text = normalize_bullets(text)
    return text.strip()


def normalize_line_for_compare(line: str) -> str:
    line = line.strip().lower()
    line = re.sub(r"\d+", "#", line)
    line = re.sub(r"\s+", " ", line)
    return line


def should_run_ocr(base_text: str, min_chars: int = 80) -> bool:
    return len(base_text.strip()) < min_chars


def preprocess_image_for_ocr(pil_image: Image.Image) -> Image.Image:
    image = pil_image.convert("L")
    width, height = image.size
    image = image.resize((max(1, width * 2), max(1, height * 2)))
    image = image.point(lambda p: 255 if p > 180 else 0)
    return image


def run_ocr_on_page(page, language: str = "eng", resolution: int = 200) -> str:
    try:
        page_image = page.to_image(resolution=resolution)
        pil_image = page_image.original
        processed = preprocess_image_for_ocr(pil_image)
        text = pytesseract.image_to_string(processed, lang=language, config="--psm 6")
        return clean_text(text)
    except Exception:
        return ""


def remove_repeating_headers_footers(page_entries: list[dict]) -> list[dict]:
    if len(page_entries) < 3:
        return page_entries

    first_lines = []
    last_lines = []

    for page in page_entries:
        lines = [l.strip() for l in page["text"].split("\n") if l.strip()]
        if not lines:
            continue

        first_lines.extend(normalize_line_for_compare(l) for l in lines[:2])
        last_lines.extend(normalize_line_for_compare(l) for l in lines[-2:])

    first_counter = Counter(first_lines)
    last_counter = Counter(last_lines)

    repeated_first = {
        line for line, count in first_counter.items()
        if count >= max(2, len(page_entries) // 3)
    }
    repeated_last = {
        line for line, count in last_counter.items()
        if count >= max(2, len(page_entries) // 3)
    }

    cleaned_pages = []

    for page in page_entries:
        lines = page["text"].split("\n")

        while lines and normalize_line_for_compare(lines[0]) in repeated_first:
            lines.pop(0)

        while lines and normalize_line_for_compare(lines[-1]) in repeated_last:
            lines.pop()

        cleaned_pages.append({
            "page": page["page"],
            "text": clean_text("\n".join(lines))
        })

    return cleaned_pages


def is_probable_heading_by_text(line: str) -> bool:
    line = line.strip()

    if not line or len(line) < 3 or len(line) > 120:
        return False

    patterns = [
        r"^\d+(\.\d+){0,4}\.?\s+[A-ZÁÄČĎÉÍĹĽŇÓÔŔŠŤÚÝŽ].+$",
        r"^(KAPITOLA|ČASŤ|PRÍLOHA)\s+\d+.*$",
        r"^(ÚVOD|ZÁVER|LITERATÚRA|POUŽITÁ LITERATÚRA|ABSTRAKT|ANOTÁCIA|OBSAH)$",
        r"^[A-ZÁÄČĎÉÍĹĽŇÓÔŔŠŤÚÝŽ][A-ZÁÄČĎÉÍĹĽŇÓÔŔŠŤÚÝŽ\s\-]{3,}$",
    ]

    if any(re.match(p, line) for p in patterns):
        return True

    if len(line) <= 60 and not line.endswith((".", ";", ",", ":")):
        words = line.split()
        if 1 <= len(words) <= 10:
            capitalized = sum(1 for w in words if w[:1].isupper())
            if capitalized >= max(1, len(words) // 2):
                return True

    return False


def extract_heading_candidates_from_page(page, top_crop=40, bottom_crop=40):
    height = page.height
    cropped = page.crop((0, top_crop, page.width, max(top_crop, height - bottom_crop)))

    words = cropped.extract_words(
        x_tolerance=2,
        y_tolerance=3,
        keep_blank_chars=False,
        use_text_flow=True,
        extra_attrs=["size", "fontname"]
    ) or []

    if not words:
        return []

    rows = []
    current_row = []
    current_top = None

    for word in words:
        word_top = round(word["top"], 1)

        if current_top is None:
            current_top = word_top
            current_row = [word]
        elif abs(word_top - current_top) <= 3:
            current_row.append(word)
        else:
            rows.append(current_row)
            current_row = [word]
            current_top = word_top

    if current_row:
        rows.append(current_row)

    candidates = []

    for row in rows:
        row = sorted(row, key=lambda w: w["x0"])
        text = " ".join(w["text"] for w in row).strip()
        avg_size = sum(float(w.get("size", 0) or 0) for w in row) / len(row)
        top = min(float(w["top"]) for w in row)

        if text:
            candidates.append({
                "text": text,
                "avg_size": avg_size,
                "top": top
            })

    return candidates


def detect_headings_from_page(page, page_text: str, top_crop=40, bottom_crop=40):
    detected = []

    candidates = extract_heading_candidates_from_page(
        page,
        top_crop=top_crop,
        bottom_crop=bottom_crop
    )

    if candidates:
        sizes = [c["avg_size"] for c in candidates if c["avg_size"] > 0]
        median_size = sorted(sizes)[len(sizes) // 2] if sizes else 0

        for c in candidates:
            text = c["text"].strip()
            if len(text) > 120:
                continue

            by_size = median_size > 0 and c["avg_size"] >= median_size * 1.15
            by_position = c["top"] < 220
            by_text = is_probable_heading_by_text(text)

            if (by_size and by_position) or by_text:
                detected.append(text)

    if not detected:
        for line in page_text.split("\n")[:12]:
            if is_probable_heading_by_text(line):
                detected.append(line.strip())

    unique = []
    seen = set()

    for item in detected:
        key = normalize_line_for_compare(item)
        if key not in seen:
            seen.add(key)
            unique.append(item)

    return unique[:3]


def count_page_images(page, min_area: int = 5000) -> int:
    images = page.images or []
    count = 0

    for img in images:
        try:
            x0 = max(0, img["x0"])
            top = max(0, img["top"])
            x1 = min(page.width, img["x1"])
            bottom = min(page.height, img["bottom"])

            width = x1 - x0
            height = bottom - top

            if width > 0 and height > 0 and (width * height) >= min_area:
                count += 1
        except Exception:
            continue

    return count


def classify_page_content(page_text: str, has_images: bool, ocr_text: str) -> str:
    combined = f"{page_text}\n{ocr_text}".lower()

    graph_keywords = [
        "graph", "chart", "line graph", "bar chart", "pie chart",
        "period", "qtr", "quarter", "axis", "trend", "legend", "graf"
    ]

    table_keywords = [
        "table", "tabuľka", "stĺpec", "riadok"
    ]

    if has_images:
        if any(keyword in combined for keyword in graph_keywords):
            return "graph"
        if any(keyword in combined for keyword in table_keywords):
            return "table"
        return "image"

    return "text"


def structure_by_headings(page_entries: list[dict]) -> list[dict]:
    sections = []
    current_section = {
        "heading": "Úvodný obsah",
        "pages": [],
        "text": []
    }

    for entry in page_entries:
        page_num = entry["page"]
        headings = entry.get("detected_headings", [])
        section_text = entry.get("final_text", "").strip()

        if headings:
            if current_section["text"]:
                current_section["text"] = clean_text("\n\n".join(current_section["text"]))
                sections.append(current_section)

            current_section = {
                "heading": headings[0],
                "pages": [page_num],
                "text": [section_text]
            }
        else:
            if page_num not in current_section["pages"]:
                current_section["pages"].append(page_num)
            if section_text:
                current_section["text"].append(section_text)

    if current_section["text"]:
        current_section["text"] = clean_text("\n\n".join(current_section["text"]))
        sections.append(current_section)

    return sections


def build_txt_output(extracted_data: dict) -> str:
    parts = []
    parts.append(f"Súbor: {extracted_data['filename']}")
    parts.append(f"Počet strán: {extracted_data['pages_count']}")
    parts.append(f"Čas spracovania: {extracted_data.get('processing_time_seconds', 0):.2f} s")
    parts.append("")

    for page in extracted_data["pages"]:
        parts.append("=" * 80)
        parts.append(f"Strana {page['page']}")
        parts.append(f"Typ obsahu: {page.get('content_type', 'text')}")

        if page.get("has_images"):
            parts.append(f"Obrázky: {page.get('images_count', 0)}")

        if page.get("detected_headings"):
            parts.append("Nadpisy:")
            for heading in page["detected_headings"]:
                parts.append(f"- {heading}")

        parts.append("")
        parts.append(page.get("final_text", ""))
        parts.append("")

    return "\n".join(parts).strip()


def build_xml_output(extracted_data: dict) -> str:
    xml_parts = [
        f'<document filename="{escape(extracted_data["filename"])}" '
        f'pages_count="{extracted_data["pages_count"]}" '
        f'processing_time_seconds="{extracted_data.get("processing_time_seconds", 0):.2f}">'
    ]

    xml_parts.append("  <pages>")
    for page in extracted_data["pages"]:
        xml_parts.append(
            f'    <page number="{page["page"]}" content_type="{escape(page.get("content_type", "text"))}" '
            f'images_count="{page.get("images_count", 0)}">'
        )

        if page.get("detected_headings"):
            xml_parts.append("      <headings>")
            for heading in page["detected_headings"]:
                xml_parts.append(f"        <heading>{escape(heading)}</heading>")
            xml_parts.append("      </headings>")

        xml_parts.append(f"      <base_text>{escape(page.get('base_text', ''))}</base_text>")
        xml_parts.append(f"      <ocr_text>{escape(page.get('ocr_text', ''))}</ocr_text>")
        xml_parts.append(f"      <final_text>{escape(page.get('final_text', ''))}</final_text>")
        xml_parts.append("    </page>")

    xml_parts.append("  </pages>")

    if extracted_data.get("sections"):
        xml_parts.append("  <sections>")
        for section in extracted_data["sections"]:
            xml_parts.append(
                f'    <section heading="{escape(section.get("heading", ""))}" '
                f'pages="{",".join(str(p) for p in section.get("pages", []))}">'
            )
            xml_parts.append(f"      <text>{escape(section.get('text', ''))}</text>")
            xml_parts.append("    </section>")
        xml_parts.append("  </sections>")

    xml_parts.append(f"  <full_text>{escape(extracted_data.get('full_text', ''))}</full_text>")
    xml_parts.append("</document>")

    return "\n".join(xml_parts)


def extract_pdf_text(
    pdf_path: str,
    structure_mode: str = "pages",
    header_crop: int = 40,
    footer_crop: int = 40,
    remove_repeated_margins: bool = True,
    use_ocr: bool = True,
    ocr_language: str = "eng",
    ocr_mode: str = "auto"
) -> dict:
    start_time = time.time()

    result = {
        "filename": os.path.basename(pdf_path),
        "pages_count": 0,
        "pages": [],
        "full_text": "",
        "sections": [],
        "processing_time_seconds": 0.0
    }

    page_entries = []

    with pdfplumber.open(pdf_path) as pdf:
        result["pages_count"] = len(pdf.pages)

        for idx, page in enumerate(pdf.pages, start=1):
            crop_top = max(0, int(header_crop))
            crop_bottom = max(0, int(footer_crop))
            bottom_y = max(crop_top, page.height - crop_bottom)

            cropped_page = page.crop((0, crop_top, page.width, bottom_y))

            extracted = cropped_page.extract_text(layout=True) or ""
            base_text = clean_text(extracted)

            ocr_text = ""
            if use_ocr:
                if ocr_mode == "fullpage":
                    ocr_text = run_ocr_on_page(cropped_page, language=ocr_language)
                elif ocr_mode == "auto" and should_run_ocr(base_text):
                    ocr_text = run_ocr_on_page(cropped_page, language=ocr_language)

            final_text = base_text if len(base_text.strip()) >= 20 else ocr_text
            final_text = clean_text(final_text)

            detected_headings = detect_headings_from_page(
                page,
                final_text or base_text,
                top_crop=crop_top,
                bottom_crop=crop_bottom
            )

            images_count = count_page_images(page)
            has_images = images_count > 0
            content_type = classify_page_content(
                page_text=base_text,
                has_images=has_images,
                ocr_text=ocr_text
            )

            page_entries.append({
                "page": idx,
                "base_text": base_text,
                "ocr_text": ocr_text,
                "final_text": final_text,
                "detected_headings": detected_headings,
                "has_images": has_images,
                "images_count": images_count,
                "content_type": content_type
            })

    if remove_repeated_margins:
        cleaned = remove_repeating_headers_footers(
            [{"page": p["page"], "text": p["final_text"]} for p in page_entries]
        )
        cleaned_map = {p["page"]: p["text"] for p in cleaned}

        for entry in page_entries:
            entry["final_text"] = cleaned_map.get(entry["page"], entry["final_text"])

    result["pages"] = page_entries
    result["full_text"] = "\n\n".join(
        page["final_text"] for page in page_entries if page["final_text"]
    ).strip()

    if structure_mode == "full":
        result["sections"] = [{
            "heading": "Celý dokument",
            "pages": list(range(1, result["pages_count"] + 1)),
            "text": result["full_text"]
        }]
    elif structure_mode == "headings":
        result["sections"] = structure_by_headings(result["pages"])
    else:
        result["sections"] = []

    result["processing_time_seconds"] = round(time.time() - start_time, 2)
    return result


def parse_form_options(req):
    structure_mode = req.form.get("structure_mode", "pages")
    ocr_mode = req.form.get("ocr_mode", "auto")
    ocr_language = req.form.get("ocr_language", "eng")

    try:
        header_crop = int(req.form.get("header_crop", 40))
    except (TypeError, ValueError):
        header_crop = 40

    try:
        footer_crop = int(req.form.get("footer_crop", 40))
    except (TypeError, ValueError):
        footer_crop = 40

    header_crop = max(0, min(header_crop, 300))
    footer_crop = max(0, min(footer_crop, 300))

    remove_repeated_margins = parse_bool_form(req.form.get("remove_repeated_margins"))
    use_ocr = parse_bool_form(req.form.get("use_ocr"))

    if structure_mode not in {"pages", "full", "headings"}:
        structure_mode = "pages"

    if ocr_mode not in {"auto", "fullpage"}:
        ocr_mode = "auto"

    if ocr_language not in {"eng", "slk", "ces"}:
        ocr_language = "eng"

    return {
        "structure_mode": structure_mode,
        "header_crop": header_crop,
        "footer_crop": footer_crop,
        "remove_repeated_margins": remove_repeated_margins,
        "use_ocr": use_ocr,
        "ocr_language": ocr_language,
        "ocr_mode": ocr_mode
    }


def save_uploaded_pdf(req_file):
    filename = secure_filename(req_file.filename)
    file_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    req_file.save(file_path)
    return filename, file_path


def process_uploaded_files(files, options):
    results = []

    for file in files:
        if not file or file.filename == "":
            continue

        if not allowed_file(file.filename):
            continue

        filename, file_path = save_uploaded_pdf(file)
        extracted_data = extract_pdf_text(file_path, **options)
        extracted_data["saved_filename"] = filename
        results.append(extracted_data)

    return results


@app.route("/", methods=["GET", "POST"])
def index():
    extracted_results = []
    error = None

    if request.method == "POST":
        files = request.files.getlist("pdf_files")

        if not files or all(f.filename == "" for f in files):
            error = "Neboli vybrané žiadne PDF súbory."
            return render_template("index.html", error=error, extracted_results=extracted_results)

        invalid_files = [f.filename for f in files if f.filename and not allowed_file(f.filename)]
        if invalid_files:
            error = "Povolené sú iba PDF súbory."
            return render_template("index.html", error=error, extracted_results=extracted_results)

        options = parse_form_options(request)

        try:
            extracted_results = process_uploaded_files(files, options)
            if not extracted_results:
                error = "Nepodarilo sa spracovať žiadny PDF súbor."
        except Exception as exc:
            error = f"Pri spracovaní PDF nastala chyba: {exc}"

    return render_template("index.html", error=error, extracted_results=extracted_results)


@app.route("/api/extract", methods=["POST"])
def api_extract():
    files = request.files.getlist("pdf_files")

    if not files or all(f.filename == "" for f in files):
        return jsonify({"error": "Neboli odoslané žiadne PDF súbory."}), 400

    invalid_files = [f.filename for f in files if f.filename and not allowed_file(f.filename)]
    if invalid_files:
        return jsonify({"error": "Povolené sú iba PDF súbory."}), 400

    options = parse_form_options(request)

    try:
        results = process_uploaded_files(files, options)
        return jsonify({"documents": results})
    except Exception as exc:
        return jsonify({"error": f"Pri spracovaní PDF nastala chyba: {exc}"}), 500


@app.route("/export/txt", methods=["POST"])
def export_txt():
    files = request.files.getlist("pdf_files")

    if not files or all(f.filename == "" for f in files):
        return Response("Neboli odoslané žiadne PDF súbory.", status=400, mimetype="text/plain; charset=utf-8")

    options = parse_form_options(request)

    try:
        results = process_uploaded_files(files, options)
        combined = []

        for doc in results:
            combined.append(build_txt_output(doc))
            combined.append("\n" + ("#" * 100) + "\n")

        txt_content = "\n".join(combined).strip()

        return Response(
            txt_content,
            mimetype="text/plain; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="pdf_extract_output.txt"'}
        )
    except Exception as exc:
        return Response(
            f"Pri spracovaní PDF nastala chyba: {exc}",
            status=500,
            mimetype="text/plain; charset=utf-8"
        )


@app.route("/export/json", methods=["POST"])
def export_json():
    files = request.files.getlist("pdf_files")

    if not files or all(f.filename == "" for f in files):
        return Response("Neboli odoslané žiadne PDF súbory.", status=400, mimetype="text/plain; charset=utf-8")

    options = parse_form_options(request)

    try:
        results = process_uploaded_files(files, options)
        json_content = json.dumps({"documents": results}, ensure_ascii=False, indent=2)

        return Response(
            json_content,
            mimetype="application/json; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="pdf_extract_output.json"'}
        )
    except Exception as exc:
        return Response(
            f"Pri spracovaní PDF nastala chyba: {exc}",
            status=500,
            mimetype="text/plain; charset=utf-8"
        )


@app.route("/export/xml", methods=["POST"])
def export_xml():
    files = request.files.getlist("pdf_files")

    if not files or all(f.filename == "" for f in files):
        return Response("Neboli odoslané žiadne PDF súbory.", status=400, mimetype="text/plain; charset=utf-8")

    options = parse_form_options(request)

    try:
        results = process_uploaded_files(files, options)

        xml_parts = ['<?xml version="1.0" encoding="UTF-8"?>', "<documents>"]
        for doc in results:
            xml_parts.append(build_xml_output(doc))
        xml_parts.append("</documents>")

        xml_content = "\n".join(xml_parts)

        return Response(
            xml_content,
            mimetype="application/xml; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="pdf_extract_output.xml"'}
        )
    except Exception as exc:
        return Response(
            f"Pri spracovaní PDF nastala chyba: {exc}",
            status=500,
            mimetype="text/plain; charset=utf-8"
        )


if __name__ == "__main__":
    app.run(debug=True)